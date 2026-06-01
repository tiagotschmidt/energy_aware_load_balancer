package main

import (
	"bytes"
	"encoding/binary"
	"encoding/csv"
	"errors"
	"flag"
	"fmt"
	"math"
	"net"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/sbinet/npyio"
)

const (
	LogDir    = "logs"
	QueryFile = "sift_data/queries.npy"
	RingSize  = 65536
)

type ParsedResponse struct {
	ServerID string
	ReqID    int
}

type StatRecord struct {
	Timestamp float64
	Status    string
	ServerID  string
	LatencyMs float64
	Rate      int
}

var (
	inflightTs   [RingSize]atomic.Int64
	inflightRate [RingSize]atomic.Int32

	statsChan = make(chan StatRecord, 100000)

	stepReplyCount atomic.Uint64
)

var ErrInvalidFormat = errors.New("invalid response format")

func main() {
	targetIP := flag.String("ip", "10.0.0.1", "Target IP")
	minRate := flag.Int("min", 10, "Minimum RPS")
	maxRate := flag.Int("max", 200, "Maximum RPS")
	step := flag.Int("step", 20, "RPS step size")
	duration := flag.Int("duration", 10, "Duration per step in seconds")
	flag.Parse()

	_ = os.MkdirAll(LogDir, 0755)

	if _, err := os.Stat(QueryFile); os.IsNotExist(err) {
		fmt.Printf("Error: Queries file not found at %s\n", QueryFile)
		os.Exit(1)
	}

	fmt.Println("--- Loading Queries ---")
	f, err := os.Open(QueryFile)
	if err != nil {
		fmt.Printf("Error opening queries file: %v\n", err)
		os.Exit(1)
	}

	var flatQueries []float32
	if err := npyio.Read(f, &flatQueries); err != nil {
		fmt.Printf("Error parsing queries.npy: %v\n", err)
		os.Exit(1)
	}
	f.Close()

	numQueries := len(flatQueries) / 128
	fmt.Printf("Loaded %d queries.\n", numQueries)

	csvFile, err := os.Create(fmt.Sprintf("%s/client_sift_experiment.csv", LogDir))
	if err != nil {
		fmt.Printf("Error creating CSV: %v\n", err)
		os.Exit(1)
	}
	defer csvFile.Close()

	csvWriter := csv.NewWriter(csvFile)
	_ = csvWriter.Write([]string{"timestamp", "status", "server_id", "latency_ms", "target_rate"})
	csvWriter.Flush()

	serverAddr, err := net.ResolveUDPAddr("udp", fmt.Sprintf("%s:8080", *targetIP))
	if err != nil {
		fmt.Printf("Error resolving address: %v\n", err)
		os.Exit(1)
	}
	conn, err := net.DialUDP("udp", nil, serverAddr)
	if err != nil {
		fmt.Printf("Error dialing UDP: %v\n", err)
		os.Exit(1)
	}

	var wg sync.WaitGroup
	wg.Add(2)

	go receiverThread(conn, &wg)
	go csvLoggerThread(csvWriter, &wg)

	fmt.Printf("--- STARTING SINGLE-SOCKET LOAD: %d -> %d RPS ---\n", *minRate, *maxRate)
	reqID := 0

	for currentRate := *minRate; currentRate <= *maxRate; currentRate += *step {
		fmt.Printf(">>> RAMPING UP: %d RPS\n", currentRate)

		stepStart := time.Now()
		stepDurationNs := int64(*duration) * int64(time.Second)

		intervalNs := int64(time.Second) / int64(currentRate)
		nextTick := time.Now().UnixNano() + intervalNs

		for time.Now().UnixNano()-stepStart.UnixNano() < stepDurationNs {
			// SPIN-LOCK: Busy wait
			for time.Now().UnixNano() < nextTick {
				// Burn CPU cycles to avoid Go scheduler latency
			}
			nextTick += intervalNs

			// Build Packet: 512 bytes (128 floats) + "ID:X"
			offset := (reqID % numQueries) * 128
			query := flatQueries[offset : offset+128]

			packet := make([]byte, 512, 532) // Pre-allocate capacity
			for i := 0; i < 128; i++ {
				binary.LittleEndian.PutUint32(packet[i*4:], math.Float32bits(query[i]))
			}
			packet = append(packet, []byte(fmt.Sprintf("ID:%d", reqID))...)

			// Track Timestamp Lock-Free
			ringIdx := reqID % RingSize
			inflightRate[ringIdx].Store(int32(currentRate))
			inflightTs[ringIdx].Store(time.Now().UnixNano())

			_, err := conn.Write(packet)
			if err != nil {
				fmt.Printf("Send Error: %v\n", err)
			}

			reqID++
		}

		time.Sleep(500 * time.Millisecond)
		count := stepReplyCount.Swap(0)
		fmt.Printf("    Step Finished. Logged %d replies at %d RPS.\n", count, currentRate)
	}

	conn.Close()
	close(statsChan)
	wg.Wait()
	fmt.Println("--- TEST FINISHED ---")
}

// Expected format: "Reply from [serverID] ID:[reqID] : Match [idx]"
func parseUDPPayload(data []byte) (ParsedResponse, error) {
	// Fast-fail checks without allocating
	if !bytes.HasPrefix(data, []byte("Reply from ")) {
		return ParsedResponse{}, ErrInvalidFormat
	}

	parts := bytes.Split(data, []byte(" "))
	if len(parts) < 4 {
		return ParsedResponse{}, ErrInvalidFormat
	}

	serverID := string(parts[2])

	var reqID int
	var idFound bool
	for _, p := range parts {
		if bytes.HasPrefix(p, []byte("ID:")) {
			idBytes := bytes.TrimPrefix(p, []byte("ID:"))
			parsedID, err := strconv.Atoi(string(idBytes))
			if err != nil {
				return ParsedResponse{}, ErrInvalidFormat
			}
			reqID = parsedID
			idFound = true
			break
		}
	}

	if !idFound {
		return ParsedResponse{}, ErrInvalidFormat
	}

	return ParsedResponse{
		ServerID: serverID,
		ReqID:    reqID,
	}, nil
}

func receiverThread(conn *net.UDPConn, wg *sync.WaitGroup) {
	defer wg.Done()
	_ = conn.SetReadBuffer(1024 * 1024 * 10)
	buf := make([]byte, 1024)

	// conn.Read will return a "use of closed network connection" error,
	// which cleanly terminates this loop.
	for {
		n, err := conn.Read(buf)
		if err != nil {
			return // Socket closed or fatal error; exit thread
		}

		recvTs := time.Now().UnixNano()

		// Parse at the boundary. If it's valid, we process it. If not, it's dropped.
		resp, err := parseUDPPayload(buf[:n])
		if err != nil {
			continue
		}

		ringIdx := resp.ReqID % RingSize
		sendTs := inflightTs[ringIdx].Swap(0) // Swap to 0 clears it
		targetRate := inflightRate[ringIdx].Load()

		if sendTs > 0 {
			latencyMs := float64(recvTs-sendTs) / 1e6

			statsChan <- StatRecord{
				Timestamp: float64(recvTs) / 1e9,
				Status:    "OK",
				ServerID:  resp.ServerID,
				LatencyMs: latencyMs,
				Rate:      int(targetRate),
			}

			stepReplyCount.Add(1)
		}
	}
}

func csvLoggerThread(writer *csv.Writer, wg *sync.WaitGroup) {
	defer wg.Done()

	count := 0
	lastFlush := time.Now()

	for stat := range statsChan {
		tsStr := fmt.Sprintf("%.6f", stat.Timestamp)
		latStr := fmt.Sprintf("%.3f", stat.LatencyMs)
		rateStr := strconv.Itoa(stat.Rate)

		_ = writer.Write([]string{tsStr, stat.Status, stat.ServerID, latStr, rateStr})
		count++

		if time.Since(lastFlush) > 2*time.Second {
			writer.Flush()
			lastFlush = time.Now()
		}
	}

	writer.Flush()
	fmt.Printf("Total responses logged: %d\n", count)
}
