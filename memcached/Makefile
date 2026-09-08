BUILD_DIR := ./build

build:
	CGO_ENABLED=0 GOOS=linux go build -o $(BUILD_DIR)/memcached_client memcached_client.go

clean: 
	@echo "Cleaning build artifacts..."
	rm -rf $(BUILD_DIR)

help:
	@echo "Makefile for memcached workload project"
	@echo "Usage:"
	@echo "  make build         - Build the memcached client"
	@echo "  make clean         - Clean build artifacts"
	@echo "  make help          - Show this help message"