BUILD_DIR := ./build

build: test build_server

build_server:
	go build -o $(BUILD_DIR)/sift_server server/sift_server.go

test:
	@echo "Running tests..."
	go test -v ./...

clean: 
	@echo "Cleaning build artifacts..."
	rm -rf $(BUILD_DIR)

help:
	@echo "Makefile for Golang SIFT project"
	@echo "Usage:"
	@echo "  make build         - Build the SIFT server and clients"
	@echo "  make test          - Run tests"
	@echo "  make clean         - Clean build artifacts"
	@echo "  make help          - Show this help message"