# SPDX-License-Identifier: Apache-2.0
# Copyright 2017-present Barefoot Networks, Inc.
# Copyright 2017-present Open Networking Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import tempfile
from time import sleep

from mininet.log import debug, error, info
from mininet.moduledeps import pathCheck
from mininet.node import Switch
from netstat import check_listening_on_port
from p4_mininet import SWITCH_START_TIMEOUT, P4Switch


class P4RuntimeSwitch(P4Switch):
    "BMv2 switch with gRPC support"

    next_grpc_port = 50051
    next_thrift_port = 9090

    def __init__(
    self,
    name,
    sw_path=None,
    json_path=None,
    grpc_port=None,
    thrift_port=None,
    pcap_dump=False,
    log_console=False,
    verbose=False,
    device_id=None,
    enable_debugger=False,
    log_file=None,
    **kwargs
):
        # --- Base class ---
        Switch.__init__(self, name, **kwargs)

        # --- Switch binary ---
        if not sw_path:
            raise AssertionError("sw_path must be provided")
        pathCheck(sw_path)
        self.sw_path = sw_path

        # --- JSON pipeline ---
        if json_path is not None:
            if not os.path.isfile(json_path):
                error(f"Invalid JSON file: {json_path}\n")
                exit(1)
        self.json_path = json_path

        # --- Ports ---
        self.grpc_port = grpc_port if grpc_port is not None else P4RuntimeSwitch.next_grpc_port
        if grpc_port is None:
            P4RuntimeSwitch.next_grpc_port += 1

        self.thrift_port = thrift_port if thrift_port is not None else P4RuntimeSwitch.next_thrift_port
        if thrift_port is None:
            P4RuntimeSwitch.next_thrift_port += 1

        if check_listening_on_port(self.grpc_port):
            error(
                "%s cannot bind port %d because it is bound by another process\n"
                % (self.name, self.grpc_port)
            )
            exit(1)

        # --- Logging / verbosity ---
        self.verbose = verbose
        self.pcap_dump = pcap_dump
        self.enable_debugger = enable_debugger
        self.log_console = log_console

        self.log_file = log_file if log_file is not None else f"{os.getcwd()}/logs/{name}.log"
        self.output = open(f"/tmp/p4s.{self.name}.log", "w")

        # --- Device ID & nanomsg ---
        if device_id is not None:
            self.device_id = device_id
            P4Switch.device_id = max(P4Switch.device_id, device_id)
        else:
            self.device_id = P4Switch.device_id
            P4Switch.device_id += 1

        self.nanomsg = f"ipc:///tmp/bm-{self.device_id}-log.ipc"

        # --- Optional CPU port ---
        self.cpu_port = kwargs.get("cpu_port")

    def check_switch_started(self, pid):
        for _ in range(SWITCH_START_TIMEOUT * 2):
            if not os.path.exists(os.path.join("/proc", str(pid))):
                return False
            if check_listening_on_port(self.grpc_port):
                return True
            sleep(0.5)

    def start(self, controllers):
        info("Starting P4 switch {}.\n".format(self.name))
        args = [self.sw_path]
        for port, intf in list(self.intfs.items()):
            if not intf.IP():
                args.extend(["-i", str(port) + "@" + intf.name])
        if self.pcap_dump:
            args.append("--pcap %s" % self.pcap_dump)
        if self.nanomsg:
            args.extend(["--nanolog", self.nanomsg])
        args.extend(["--device-id", str(self.device_id)])
        P4Switch.device_id += 1
        if self.json_path:
            args.append(self.json_path)
        else:
            args.append("--no-p4")
        if self.enable_debugger:
            args.append("--debugger")
        if self.log_console:
            args.append("--log-console")
        if self.thrift_port:
            args.append("--thrift-port " + str(self.thrift_port))
        if self.grpc_port:
            args.append("-- --grpc-server-addr 0.0.0.0:" + str(self.grpc_port))
        if self.cpu_port:
            args.append("--cpu-port " + str(self.cpu_port))
        cmd = " ".join(args)
        info(cmd + "\n")
        print(cmd + "\n")

        pid = None
        with tempfile.NamedTemporaryFile() as f:
            self.cmd(cmd + " >" + self.log_file + " 2>&1 & echo $! >> " + f.name)
            pid = int(f.read())
        debug("P4 switch {} PID is {}.\n".format(self.name, pid))
        if not self.check_switch_started(pid):
            error("P4 switch {} did not start correctly.\n".format(self.name))
            exit(1)
        info("P4 switch {} has been started.\n".format(self.name))
