package main

import (
	"encoding/binary"
	"fmt"
	"math"
	"net"
	"os"
	"sync/atomic"
	"time"

	"github.com/alecthomas/kingpin"
	"github.com/sbinet/npyio"
)

const LOG_DIR = "logs"
const DATA_FILE = "sift_data/dataset.npy"
const MAX_VECTORS = 100000

var (
	port = kingpin.Flag("port", "Port to listen on").Default("8080").String()
	id   = kingpin.Flag("id", "Unique identifier for this server instance").Default("server1").String()
)

var globalRequestCounter uint64 = 0

func vectorSearch(query []float32, database []float32) int {
	var minDist float32 = math.MaxFloat32
	nearestIdx := 0

	// Iterate through every vector (each is 128 elements long)
	for i := 0; i < MAX_VECTORS; i++ {
		var sqDist float32 = 0.0
		offset := i * 128

		for j := 0; j < 128; j++ {
			diff := database[offset+j] - query[j]
			sqDist += diff * diff

			// Optimization: stop if this vector is already worse than our current best
			if sqDist > minDist {
				break
			}
		}

		if sqDist < minDist {
			minDist = sqDist
			nearestIdx = i
		}
	}

	return nearestIdx
}

func monitorThroughput(identity string, intervalMS int) {
	var filename = fmt.Sprintf("%s/%s_throughput.txt", LOG_DIR, identity)

	fmt.Printf("--- Monitor started. Writing throughput to %s ---\n", filename)

	for true {
		time.Sleep(time.Millisecond * time.Duration(intervalMS))

		requestCount := atomic.SwapUint64(&globalRequestCounter, 0)
		var currentThroughput = float64(requestCount) / float64(intervalMS) / 1000.0 // todo create atomic requestCounter

		tempFilename := fmt.Sprintf("%s.tmp", filename)

		writeString := fmt.Sprintf("%.2f", currentThroughput)
		err := os.WriteFile(tempFilename, []byte(writeString), 0644)
		if err != nil {
			fmt.Printf("Error: Could not write to temp file %s: %v\n", tempFilename, err)
			continue
		}

		os.Rename(tempFilename, filename)
	}
}

func parseQueryVector(data []byte) []float32 {
	returnVector := make([]float32, 128)

	for i := 0; i < 128; i++ {
		start := i * 4
		var currentValue = data[start : start+4]
		var currentBits = binary.LittleEndian.Uint32(currentValue)
		var currentFloat = math.Float32frombits(currentBits)

		returnVector[i] = currentFloat
	}

	return returnVector
}

func handle_request(databaseVectors []float32, socket *net.UDPConn, address *net.UDPAddr, identity string, csvFile *os.File, messageData []byte) {
	var startProcessing = time.Now()

	if len(messageData) == 0 {
		fmt.Printf("Received empty message from %s\n", address)
		return
	}

	if len(messageData) < 512 {
		fmt.Printf("Received message too short from %s: %d bytes\n", address, len(messageData))
		return
	}

	queryVector := parseQueryVector(messageData[:512]) // TODO: implement this function

	requestID := string(messageData[515:])

	result := vectorSearch(queryVector, databaseVectors) // TODO: implement this function
	// fmt.Printf("Result: %d\n", result)

	reply := fmt.Sprintf("Reply from %s ID:%s : Match %d", identity, requestID, result)

	socket.WriteToUDP([]byte(reply), address)

	durationMs := time.Since(startProcessing).Milliseconds()
	csvFile.WriteString(fmt.Sprintf("%s,%s,%d,", startProcessing.Format(time.RFC3339), address.String(), durationMs))

	// TODO INCREMENT requestCounbter
	atomic.AddUint64(&globalRequestCounter, 1)
}

func loadDatabase(filename string) ([]float32, error) {
	var returnDatabase []float32
	f, err := os.Open(filename)
	if err != nil {
		return returnDatabase, err
	}
	defer f.Close()

	err = npyio.Read(f, &returnDatabase)
	return returnDatabase, err
}

func main() {
	kingpin.Parse()

	fmt.Printf("--- Loading SIFT data (%d vectors)---\n", MAX_VECTORS)
	var database []float32

	if _, err := os.Stat(DATA_FILE); err == nil {
		database, err = loadDatabase(DATA_FILE)
		if err != nil {
			fmt.Printf("Error: Could not load SIFT data from %s:%s\n", DATA_FILE, err)
			os.Exit(1)
		}
	} else {
		fmt.Printf("Error: SIFT data file not found at %s\n", DATA_FILE)
		os.Exit(1)
	}

	fmt.Printf("--- Loaded SIFT data (%d vectors)---\n", MAX_VECTORS)

	addr, err := net.ResolveUDPAddr("udp", ":"+*port)
	if err != nil {
		fmt.Printf("Error resolving address: %v\n", err)
		os.Exit(1)
	}

	socket, err := net.ListenUDP("udp", addr)
	if err != nil {
		fmt.Printf("Error starting UDP listener: %v\n", err)
		fmt.Println("TIP: Check if another server is already running on this port.")
		os.Exit(1)
	}
	defer socket.Close()

	csvFile, _ := os.Create(fmt.Sprintf("%s/%s_work.csv", LOG_DIR, *id))

	go monitorThroughput(*id, 1000)

	fmt.Printf("--- Server Listening on UDP %s ---\n", *port)

	for true {
		data := make([]byte, 2048)
		n, address, err := socket.ReadFromUDP(data)

		if err != nil {
			fmt.Printf("Error: %v\n", err)
			continue
		}

		go handle_request(database, socket, address, *id, csvFile, data[:n])
	}
}
