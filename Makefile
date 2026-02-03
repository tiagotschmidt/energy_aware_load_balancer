# SPDX-License-Identifier: Apache-2.0

# Project Directories
BUILD_DIR = build
PCAP_DIR = pcaps
LOG_DIR = logs

# Compiler Configuration
P4C = p4c-bm2-ss
# Since $@ will be "build/load_balance.json", basename will be "build/load_balance"
P4C_ARGS += --p4runtime-files $(basename $@).p4info.txtpb

RUN_SCRIPT = ./p4-utils/run_exercise.py

ifndef TOPO
TOPO = topology.json
endif

# Find source in p4src/ and map to build/ targets
source = $(wildcard p4src/*.p4)
compiled_json := $(patsubst p4src/%.p4, $(BUILD_DIR)/%.json, $(source))

DEFAULT_JSON = $(firstword $(compiled_json))

# Define NO_P4 to start BMv2 without a program
ifndef NO_P4
run_args += -j $(DEFAULT_JSON)
endif

# Set BMV2_SWITCH_EXE to override the BMv2 target
ifdef BMV2_SWITCH_EXE
run_args += -b $(BMV2_SWITCH_EXE)
endif

all: run

run: build
	sudo PATH=$(PATH) ${P4_EXTRA_SUDO_OPTS} python3 $(RUN_SCRIPT) -t $(TOPO) $(run_args)

stop:
	sudo PATH=$(PATH) `which mn` -c

build: dirs $(compiled_json)

dirs:
	mkdir -p $(BUILD_DIR) $(PCAP_DIR) $(LOG_DIR)

# Rule to compile from p4src/ to build/
$(BUILD_DIR)/%.json: p4src/%.p4
	$(P4C) --p4v 16 $(P4C_ARGS) -o $@ $<

clean: stop
	rm -f *.pcap
	rm -rf $(BUILD_DIR) $(PCAP_DIR) $(LOG_DIR)