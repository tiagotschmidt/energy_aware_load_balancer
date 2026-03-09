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
const MAX_VECTORS = 1000000

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
	var filename = fmt.Sprintf("%s/%sthroughput.log", LOG_DIR, identity)

	fmt.Printf("--- Monitor started. Writing throughput to %s ---\n", filename)

	for true {
		time.Sleep(time.Millisecond * time.Duration(intervalMS))

		requestCount := atomic.SwapUint64(&globalRequestCounter, 0)
		var currentThroughput = float64(requestCount) / float64(intervalMS) / 1000.0 // todo create atomic requestCounter

		tempFilename := fmt.Sprintf("%s.tmp", filename)

		file, err := os.Open(tempFilename)
		defer file.Close()
		if err != nil {
			fmt.Printf("Error: Could not open log file %s:%s\n", tempFilename, err)
			return
		}

		writeString := fmt.Sprintf("%.2f", currentThroughput)
		file.Write([]byte(writeString))

		os.Rename(tempFilename, filename)
	}
}

func parseQueryVector(data []byte) []float32 {
	returnVector := make([]float32, 128)

	for i := 0; i < 128; i++ {
		var currentValue = data[i : i+4]
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
		queryVector := parseQueryVector(messageData[:512]) // TODO: implement this function

		requestID := string(messageData[515:])

		result := vectorSearch(queryVector, databaseVectors) // TODO: implement this function
		reply := fmt.Sprintf("Reply from %s ID:%s : Match %d", identity, requestID, result)

		socket.WriteToUDP([]byte(reply), address)

		durationMs := time.Since(startProcessing).Milliseconds()
		csvFile.WriteString(fmt.Sprintf("%s,%s,%d,", startProcessing.Format(time.RFC3339), address.String(), durationMs))

		// TODO INCREMENT requestCounbter
		atomic.AddUint64(&globalRequestCounter, 1)
	}
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

	socket, _ := net.ListenUDP(*port, &net.UDPAddr{})

	csvFile, _ := os.Create(fmt.Sprintf("%s/%s_work.csv", LOG_DIR, *id))

	go monitorThroughput(*id, 1000)

	for true {
		data := make([]byte, 2048)
		_, address, _ := socket.ReadFromUDP(data)
		go handle_request(database, socket, address, *id, csvFile, data)
	}
}
