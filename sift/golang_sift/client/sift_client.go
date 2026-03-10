package main

import (
	"encoding/binary"
	"encoding/csv"
	"flag"
	"fmt"
	"math"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/sbinet/npyio"
)

const (
	LogDir    = "logs"
	QueryFile = "sift_data/queries.npy"
)

// InflightReq stores the send time and the rate at which it was sent
type InflightReq struct {
	SendTs time.Time
	Rate   int
}

// StatRecord represents a single log entry
type StatRecord struct {
	Timestamp float64
	Status    string
	ServerID  string
	LatencyMs float64
	Rate      int
}

var (
	inflight   = make(map[int]InflightReq)
	inflightMu sync.Mutex

	statsChan = make(chan StatRecord, 100000)
	stopEvent atomic.Bool

	stepReplyCount uint64
)

func main() {
	targetIP := flag.String("ip", "10.0.0.1", "Target IP")
	minRate := flag.Int("min", 10, "Minimum RPS")
	maxRate := flag.Int("max", 200, "Maximum RPS")
	step := flag.Int("step", 20, "RPS step size")
	duration := flag.Int("duration", 10, "Duration per step in seconds")
	flag.Parse()

	// 1. Ensure logs dir exists
	_ = os.MkdirAll(LogDir, 0755)

	// 2. Load Queries
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

	// 3. Setup CSV File
	csvFile, err := os.Create(fmt.Sprintf("%s/client_sift_experiment.csv", LogDir))
	if err != nil {
		fmt.Printf("Error creating CSV: %v\n", err)
		os.Exit(1)
	}
	defer csvFile.Close()

	csvWriter := csv.NewWriter(csvFile)
	_ = csvWriter.Write([]string{"timestamp", "status", "server_id", "latency_ms", "target_rate"})
	csvWriter.Flush()

	// 4. Setup UDP Socket (Persistent)
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
	defer conn.Close()

	// 5. Start Background Workers
	var wg sync.WaitGroup
	wg.Add(2)

	go receiverThread(conn, &wg)
	go csvLoggerThread(csvWriter, &wg)

	// 6. Main Open-Loop Load Generator
	fmt.Printf("--- STARTING SINGLE-SOCKET LOAD: %d -> %d RPS ---\n", *minRate, *maxRate)
	reqID := 0

	for currentRate := *minRate; currentRate <= *maxRate; currentRate += *step {
		fmt.Printf(">>> RAMPING UP: %d RPS\n", currentRate)

		stepStart := time.Now()
		stepDuration := time.Duration(*duration) * time.Second

		// Use a Ticker for precise open-loop rate control
		ticker := time.NewTicker(time.Second / time.Duration(currentRate))

		for time.Since(stepStart) < stepDuration {
			<-ticker.C // Wait for the exact tick

			// Build Packet: 512 bytes (128 floats) + "ID:X"
			offset := (reqID % numQueries) * 128
			query := flatQueries[offset : offset+128]

			packet := make([]byte, 512)
			for i := 0; i < 128; i++ {
				binary.LittleEndian.PutUint32(packet[i*4:], math.Float32bits(query[i]))
			}
			idString := fmt.Sprintf("ID:%d", reqID)
			packet = append(packet, []byte(idString)...)

			// Track Timestamp
			inflightMu.Lock()
			inflight[reqID] = InflightReq{SendTs: time.Now(), Rate: currentRate}
			inflightMu.Unlock()

			// Send
			_, err := conn.Write(packet)
			if err != nil {
				fmt.Printf("Send Error: %v\n", err)
			}

			reqID++
		}
		ticker.Stop()
		time.Sleep(500 * time.Millisecond)
		count := atomic.SwapUint64(&stepReplyCount, 0)
		fmt.Printf("    Step Finished. Logged %d replies at %d RPS.\n", count, currentRate)
	}

	// 7. Cleanup
	stopEvent.Store(true)
	close(statsChan)
	wg.Wait()
	fmt.Println("--- TEST FINISHED ---")
}

func receiverThread(conn *net.UDPConn, wg *sync.WaitGroup) {
	defer wg.Done()
	_ = conn.SetReadBuffer(1024 * 1024 * 10) // Large buffer to prevent packet loss
	buf := make([]byte, 1024)

	for !stopEvent.Load() {
		// Set a short deadline so we can periodically check stopEvent
		_ = conn.SetReadDeadline(time.Now().Add(500 * time.Millisecond))
		n, err := conn.Read(buf)
		if err != nil {
			if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
				continue // Normal timeout, loop again
			}
			continue
		}

		recvTs := time.Now()
		resp := string(buf[:n])

		// Expected format: "Reply from [serverID] ID:[reqID] : Match [idx]"
		if strings.Contains(resp, "Reply from") && strings.Contains(resp, "ID:") {
			parts := strings.Split(resp, " ")
			if len(parts) >= 4 {
				serverID := parts[2]

				var reqID int
				for _, p := range parts {
					if strings.HasPrefix(p, "ID:") {
						idStr := strings.TrimPrefix(p, "ID:")
						reqID, _ = strconv.Atoi(idStr)
						break
					}
				}

				// Calculate Latency
				inflightMu.Lock()
				reqInfo, exists := inflight[reqID]
				if exists {
					delete(inflight, reqID) // Cleanup
				}
				inflightMu.Unlock()

				latencyMs := 0.0
				targetRate := 0
				if exists {
					latencyMs = float64(recvTs.Sub(reqInfo.SendTs).Nanoseconds()) / 1e6
					targetRate = reqInfo.Rate
				}

				// Send to logger
				stat := StatRecord{
					Timestamp: float64(recvTs.UnixNano()) / 1e9,
					Status:    "OK",
					ServerID:  serverID,
					LatencyMs: latencyMs,
					Rate:      targetRate,
				}
				statsChan <- stat

				atomic.AddUint64(&stepReplyCount, 1)
			}
		}
	}
}

func csvLoggerThread(writer *csv.Writer, wg *sync.WaitGroup) {
	defer wg.Done()

	count := 0
	lastFlush := time.Now()

	// Range will automatically exit when statsChan is closed by main()
	for stat := range statsChan {
		tsStr := fmt.Sprintf("%.6f", stat.Timestamp)
		latStr := fmt.Sprintf("%.3f", stat.LatencyMs)
		rateStr := strconv.Itoa(stat.Rate)

		_ = writer.Write([]string{tsStr, stat.Status, stat.ServerID, latStr, rateStr})
		count++

		// Flush periodically to avoid blocking memory, mimicking the Python batch write
		if time.Since(lastFlush) > 2*time.Second {
			writer.Flush()
			lastFlush = time.Now()
		}
	}

	writer.Flush()
	fmt.Printf("Total responses logged: %d\n", count)
}
