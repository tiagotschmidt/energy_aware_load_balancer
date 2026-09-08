package main

import (
	"encoding/csv"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"strconv"
	"sync"
	"sync/atomic"
	"time"
)

const (
	LogDir   = "logs"
	RingSize = 65536
)

type ParsedResponse struct {
	ReqID int
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
	minRate := flag.Int("min", 1000, "Minimum RPS")
	maxRate := flag.Int("max", 35000, "Maximum RPS")
	step := flag.Int("step", 1000, "RPS step size")
	duration := flag.Int("duration", 60, "Duration per step in seconds")
	flag.Parse()

	_ = os.MkdirAll(LogDir, 0755)

	csvFile, err := os.Create(fmt.Sprintf("%s/client_memcached_experiment.csv", LogDir))
	if err != nil {
		fmt.Printf("Error creating CSV: %v\n", err)
		os.Exit(1)
	}
	defer csvFile.Close()

	csvWriter := csv.NewWriter(csvFile)
	_ = csvWriter.Write([]string{"timestamp", "status", "server_id", "latency_ms", "target_rate"})
	csvWriter.Flush()

	serverAddr, err := net.ResolveUDPAddr("udp", fmt.Sprintf("%s:11211", *targetIP))
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

	fmt.Printf("--- STARTING MEMCACHED UDP SWEEP: %d -> %d RPS ---\n", *minRate, *maxRate)
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

			// Memcached UDP Header (8 bytes):
			// Bytes 0-1: Request ID
			// Bytes 2-3: Sequence number (0)
			// Bytes 4-5: Total number of datagrams (1)
			// Bytes 6-7: Reserved (0)
			cmd := fmt.Sprintf("get key_%d\r\n", reqID%100000)
			cmdBytes := []byte(cmd)

			packet := make([]byte, 8+len(cmdBytes))

			// Map Request ID to the first 2 bytes
			reqIDUint16 := uint16(reqID & 0xFFFF)
			packet[0] = byte(reqIDUint16 >> 8)
			packet[1] = byte(reqIDUint16 & 0xFF)

			// Hardcoded standard Memcached UDP protocol values
			packet[2] = 0 // Seq = 0
			packet[3] = 0
			packet[4] = 0 // Total datagrams = 1
			packet[5] = 1
			packet[6] = 0 // Reserved = 0
			packet[7] = 0

			copy(packet[8:], cmdBytes)

			// Track Timestamp Lock-Free
			ringIdx := reqID % RingSize
			inflightRate[ringIdx].Store(int32(currentRate))
			inflightTs[ringIdx].Store(time.Now().UnixNano())

			_, err := conn.Write(packet)
			if err != nil {
				// Non-fatal send error silently ignored to preserve loop speed
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

// Parses the 8-byte Memcached UDP response header
func parseUDPPayload(data []byte) (ParsedResponse, error) {
	if len(data) < 8 {
		return ParsedResponse{}, ErrInvalidFormat
	}

	// Reconstruct the Request ID from the first 2 bytes
	reqID := (int(data[0]) << 8) | int(data[1])

	return ParsedResponse{
		ReqID: reqID,
	}, nil
}

func receiverThread(conn *net.UDPConn, wg *sync.WaitGroup) {
	defer wg.Done()
	_ = conn.SetReadBuffer(1024 * 1024 * 10)
	buf := make([]byte, 2048)

	for {
		n, err := conn.Read(buf)
		if err != nil {
			return // Socket closed or fatal error; exit thread
		}

		recvTs := time.Now().UnixNano()

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
				ServerID:  "VIP", // Standard Memcached does not return its hostname
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

		// Matches SIFT CSV schema perfectly
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
