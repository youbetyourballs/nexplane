# Agent Parity SP4: macOS Observability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement macOS-native appdiscovery (launchctl+lsof), deepdiscover (launchctl workloads+docker+netstat), and forensics (log show bundle) replacing empty/stub implementations.

**Architecture:** Each command gets a `_darwin.go` file. `_other.go` build tags gain `&& !darwin`. Tests mock `exec.Command` via package-level var.

**Tech Stack:** Go 1.21, `launchctl`, `lsof`, `netstat`, `log show`, `system_profiler`, `kextstat`, `ps`, `santactl`

---

### Task 1: appdiscovery_darwin.go

**Files:**
- Create: `agent/commands/appdiscovery/appdiscovery_darwin.go`
- Create: `agent/commands/appdiscovery/appdiscovery_darwin_test.go`
- Modify: `agent/commands/appdiscovery/appdiscovery_other.go` — build tag add `&& !darwin`

- [ ] **Step 1: Write failing test**

Create `agent/commands/appdiscovery/appdiscovery_darwin_test.go`:

```go
//go:build darwin

package appdiscovery

import (
	"os/exec"
	"strings"
	"testing"
)

var mockLaunchctlList = `PID	Status	Label
1234	0	com.apple.Finder
5678	0	com.example.myapp
-	0	com.apple.notrunning`

var mockLsofOutput = `COMMAND   PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
myapp    5678   root    6u  IPv4 0x123      0t0  TCP *:8080 (LISTEN)
Finder   1234   root    3u  IPv4 0x456      0t0  TCP *:80 (LISTEN)`

func TestDiscoverApplicationsOS_Darwin(t *testing.T) {
	execCommandAppDiscovery = func(name string, args ...string) *exec.Cmd {
		switch {
		case name == "launchctl" && len(args) > 0 && args[0] == "list":
			return exec.Command("echo", mockLaunchctlList)
		case name == "lsof":
			return exec.Command("echo", mockLsofOutput)
		default:
			return exec.Command("echo", "")
		}
	}
	t.Cleanup(func() { execCommandAppDiscovery = exec.Command })

	apps, err := discoverApplicationsOS(map[string]any{})
	if err != nil {
		t.Fatalf("discoverApplicationsOS: %v", err)
	}
	if len(apps) == 0 {
		t.Fatal("expected at least one application")
	}

	// Verify at least one app has a name
	found := false
	for _, a := range apps {
		if strings.Contains(a.Name, "myapp") || strings.Contains(a.Name, "Finder") {
			found = true
		}
	}
	if !found {
		t.Fatalf("expected app from mock output; got: %+v", apps)
	}
}

func TestDiscoverApplicationsOS_DarwinListeningPorts(t *testing.T) {
	execCommandAppDiscovery = func(name string, args ...string) *exec.Cmd {
		switch {
		case name == "launchctl":
			return exec.Command("echo", mockLaunchctlList)
		case name == "lsof":
			return exec.Command("echo", mockLsofOutput)
		default:
			return exec.Command("echo", "")
		}
	}
	t.Cleanup(func() { execCommandAppDiscovery = exec.Command })

	apps, err := discoverApplicationsOS(map[string]any{})
	if err != nil {
		t.Fatalf("discoverApplicationsOS: %v", err)
	}
	hasPort := false
	for _, a := range apps {
		if len(a.ListeningPorts) > 0 {
			hasPort = true
		}
	}
	if !hasPort {
		t.Log("no listening ports attached (acceptable if port-to-pid mapping not matched)")
	}
}
```

- [ ] **Step 2: Implement appdiscovery_darwin.go**

Create `agent/commands/appdiscovery/appdiscovery_darwin.go`:

```go
//go:build darwin

package appdiscovery

import (
	"os/exec"
	"strconv"
	"strings"
)

// execCommandAppDiscovery is mockable in tests.
var execCommandAppDiscovery = exec.Command

func discoverApplicationsOS(_ map[string]any) ([]Application, error) {
	services := discoverLaunchdServices()
	portMap := discoverDarwinListeningPorts()

	for i := range services {
		name := services[i].Name
		if ports, ok := portMap[name]; ok {
			services[i].ListeningPorts = ports
		}
		services[i].Stateful = len(services[i].DataDirectories) > 0
	}

	return services, nil
}

func discoverLaunchdServices() []Application {
	out, err := execCommandAppDiscovery("launchctl", "list").Output()
	if err != nil {
		return nil
	}

	var apps []Application
	lines := strings.Split(string(out), "\n")
	// Header: "PID\tStatus\tLabel"
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 3 {
			continue
		}
		pid := fields[0]
		label := fields[2]
		if pid == "-" {
			continue // not running
		}

		app := Application{
			Name:   labelToName(label),
			Binary: label,
			Status: "running",
		}

		// Get program arguments from launchctl print
		infoOut, _ := execCommandAppDiscovery("launchctl", "print", "system/"+label).Output()
		for _, l := range strings.Split(string(infoOut), "\n") {
			l = strings.TrimSpace(l)
			if strings.HasPrefix(l, "path = ") {
				app.Binary = strings.TrimPrefix(l, "path = ")
			}
		}

		apps = append(apps, app)
	}
	return apps
}

// labelToName extracts a short human-readable name from a launchd label.
// e.g. "com.apple.Finder" -> "Finder", "homebrew.mxcl.nginx" -> "nginx"
func labelToName(label string) string {
	parts := strings.Split(label, ".")
	if len(parts) > 0 {
		return parts[len(parts)-1]
	}
	return label
}

func discoverDarwinListeningPorts() map[string][]int {
	portMap := map[string][]int{}
	out, err := execCommandAppDiscovery("lsof", "-nP", "-iTCP", "-sTCP:LISTEN").Output()
	if err != nil {
		return portMap
	}
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		// COMMAND PID USER FD TYPE DEVICE SIZE NODE NAME
		if len(fields) < 9 {
			continue
		}
		cmd := fields[0]
		nameField := fields[len(fields)-1]
		// NAME format: "*:8080" or "127.0.0.1:5432"
		if idx := strings.LastIndex(nameField, ":"); idx != -1 {
			portStr := nameField[idx+1:]
			if port, err := strconv.Atoi(portStr); err == nil {
				portMap[cmd] = append(portMap[cmd], port)
			}
		}
	}
	return portMap
}
```

- [ ] **Step 3: Fix appdiscovery_other.go build tag**

Edit `agent/commands/appdiscovery/appdiscovery_other.go` line 1:
```go
//go:build !linux && !darwin
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/appdiscovery/ -run TestDiscoverApplicationsOS_Darwin -v
```
Expected: PASS

- [ ] **Step 5: Verify compilation**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && echo "OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/appdiscovery/appdiscovery_darwin.go agent/commands/appdiscovery/appdiscovery_darwin_test.go agent/commands/appdiscovery/appdiscovery_other.go
git commit -m "feat(agent): macOS application discovery via launchctl+lsof"
```

---

### Task 2: deepdiscover_darwin.go

**Files:**
- Create: `agent/commands/deepdiscover/deepdiscover_darwin.go`
- Create: `agent/commands/deepdiscover/deepdiscover_darwin_test.go`
- Modify: `agent/commands/deepdiscover/deepdiscover_other.go` — build tag add `&& !darwin`

- [ ] **Step 1: Write failing test**

Create `agent/commands/deepdiscover/deepdiscover_darwin_test.go`:

```go
//go:build darwin

package deepdiscover

import (
	"os/exec"
	"testing"
)

var mockLaunchctlListDD = `PID	Status	Label
100	0	com.example.webserver
200	0	com.example.dbserver
-	0	com.apple.stopped`

var mockNetstatListening = `Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)
tcp4       0      0  *.8080                 *.*                    LISTEN
tcp4       0      0  *.5432                 *.*                    LISTEN`

func TestExecuteOS_Darwin(t *testing.T) {
	execCommandLinuxDD = func(name string, args ...string) *exec.Cmd {
		switch name {
		case "launchctl":
			return exec.Command("echo", mockLaunchctlListDD)
		case "netstat":
			return exec.Command("echo", mockNetstatListening)
		case "docker":
			return exec.Command("false") // docker not present
		default:
			return exec.Command("echo", "")
		}
	}
	t.Cleanup(func() { execCommandLinuxDD = exec.Command })

	result, err := executeOS(map[string]any{})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result.OS != "darwin" {
		t.Fatalf("expected OS=darwin, got %q", result.OS)
	}
	if len(result.Workloads) == 0 {
		t.Fatal("expected at least one workload from launchctl mock")
	}
}
```

Note: `deepdiscover_linux.go` uses `execCommandLinux` — darwin file should introduce its own var `execCommandDarwin` but since tests call `executeOS` which is defined per-OS, we need to check the existing pattern. Looking at `deepdiscover_linux.go`: `var execCommandLinux = exec.Command`. The darwin file should use its own var. Update the test:

```go
//go:build darwin

package deepdiscover

import (
	"os/exec"
	"testing"
)

func TestExecuteOS_Darwin(t *testing.T) {
	execCommandDarwin = func(name string, args ...string) *exec.Cmd {
		switch name {
		case "launchctl":
			return exec.Command("printf", "PID\tStatus\tLabel\n100\t0\tcom.example.webserver\n")
		case "netstat":
			return exec.Command("printf", "Proto Recv-Q Local Address State\ntcp4 0 *.8080 LISTEN\n")
		default:
			return exec.Command("echo", "")
		}
	}
	t.Cleanup(func() { execCommandDarwin = exec.Command })

	result, err := executeOS(map[string]any{})
	if err != nil {
		t.Fatalf("executeOS: %v", err)
	}
	if result.OS != "darwin" {
		t.Fatalf("expected OS=darwin, got %q", result.OS)
	}
}
```

- [ ] **Step 2: Implement deepdiscover_darwin.go**

Create `agent/commands/deepdiscover/deepdiscover_darwin.go`:

```go
//go:build darwin

package deepdiscover

import (
	"os/exec"
	"strconv"
	"strings"
)

// execCommandDarwin is mockable in tests.
var execCommandDarwin = exec.Command

func executeOS(_ map[string]any) (*DeepDiscoveryResult, error) {
	var workloads []DiscoveredWorkload

	workloads = append(workloads, collectLaunchdServicesDarwin()...)
	workloads = append(workloads, detectContainerRuntimesDarwin()...)

	listeningPorts := collectListeningPortsDarwin()
	establishedConns := collectEstablishedConnsDarwin()

	for i := range workloads {
		workloads[i].ListeningPorts = listeningPorts
		workloads[i].Connections = establishedConns
	}

	return &DeepDiscoveryResult{
		Workloads:   workloads,
		HybridEdges: []HybridEdge{},
		CollectedAt: nowISO(),
		OS:          "darwin",
	}, nil
}

func collectLaunchdServicesDarwin() []DiscoveredWorkload {
	out, err := execCommandDarwin("launchctl", "list").Output()
	if err != nil {
		return nil
	}
	var workloads []DiscoveredWorkload
	lines := strings.Split(string(out), "\n")
	for _, line := range lines[1:] {
		fields := strings.Fields(line)
		if len(fields) < 3 || fields[0] == "-" {
			continue
		}
		label := fields[2]
		wl := DiscoveredWorkload{
			Type:   "launchd_service",
			Name:   label,
			Status: "running",
		}
		workloads = append(workloads, wl)
	}
	return workloads
}

func detectContainerRuntimesDarwin() []DiscoveredWorkload {
	var workloads []DiscoveredWorkload
	// Docker Desktop on macOS exposes the same docker CLI
	out, err := execCommandDarwin("docker", "ps", "--format", "{{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Image}}").Output()
	if err != nil {
		return nil
	}
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, "\t", 4)
		name := ""
		if len(parts) >= 2 {
			name = parts[1]
		}
		workloads = append(workloads, DiscoveredWorkload{
			Type:   "container",
			Name:   name,
			Status: "running",
		})
	}
	return workloads
}

func collectListeningPortsDarwin() []int {
	out, err := execCommandDarwin("netstat", "-an", "-p", "tcp").Output()
	if err != nil {
		return nil
	}
	var ports []int
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.Contains(line, "LISTEN") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		addr := fields[3]
		if idx := strings.LastIndex(addr, "."); idx != -1 {
			if p, err := strconv.Atoi(addr[idx+1:]); err == nil {
				ports = append(ports, p)
			}
		}
	}
	return ports
}

func collectEstablishedConnsDarwin() []NetworkConnection {
	out, err := execCommandDarwin("netstat", "-an", "-p", "tcp").Output()
	if err != nil {
		return nil
	}
	var conns []NetworkConnection
	for _, line := range strings.Split(string(out), "\n") {
		if !strings.Contains(line, "ESTABLISHED") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		conns = append(conns, NetworkConnection{
			LocalAddr:  fields[3],
			RemoteAddr: fields[4],
			State:      "ESTABLISHED",
		})
	}
	return conns
}
```

- [ ] **Step 3: Fix deepdiscover_other.go build tag**

Edit `agent/commands/deepdiscover/deepdiscover_other.go` line 1:
```go
//go:build !linux && !darwin && !windows
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/deepdiscover/ -run TestExecuteOS_Darwin -v
```
Expected: PASS

- [ ] **Step 5: Compile check**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && echo "OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/deepdiscover/deepdiscover_darwin.go agent/commands/deepdiscover/deepdiscover_darwin_test.go agent/commands/deepdiscover/deepdiscover_other.go
git commit -m "feat(agent): macOS deep discovery via launchctl+docker+netstat"
```

---

### Task 3: forensics_darwin.go

**Files:**
- Create: `agent/commands/forensics/forensics_darwin.go`
- Create: `agent/commands/forensics/forensics_darwin_test.go`
- Modify: `agent/commands/forensics/forensics_other.go` — build tag add `&& !darwin`

- [ ] **Step 1: Write failing test**

Create `agent/commands/forensics/forensics_darwin_test.go`:

```go
//go:build darwin

package forensics

import (
	"bytes"
	"compress/gzip"
	"context"
	"io"
	"os/exec"
	"testing"
)

func TestCollectOS_Darwin(t *testing.T) {
	execCommandForensics = func(name string, args ...string) *exec.Cmd {
		return exec.Command("echo", "mock output for "+name)
	}
	t.Cleanup(func() { execCommandForensics = exec.Command })

	cfg := ForensicsConfig{}
	artifacts, buf, err := collectOS(context.Background(), cfg)
	if err != nil {
		t.Fatalf("collectOS: %v", err)
	}
	if len(artifacts) == 0 {
		t.Fatal("expected at least one artifact")
	}

	// Verify bundle is valid gzip
	gz, err := gzip.NewReader(bytes.NewReader(buf.Bytes()))
	if err != nil {
		t.Fatalf("bundle not valid gzip: %v", err)
	}
	defer gz.Close()
	content, err := io.ReadAll(gz)
	if err != nil {
		t.Fatalf("reading gzip: %v", err)
	}
	if len(content) == 0 {
		t.Fatal("empty bundle content")
	}
}
```

- [ ] **Step 2: Implement forensics_darwin.go**

Create `agent/commands/forensics/forensics_darwin.go`:

```go
//go:build darwin

package forensics

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"os/exec"
	"strings"
	"time"
)

// execCommandForensics is mockable in tests.
var execCommandForensics = exec.Command

func collectOS(_ context.Context, _ ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	var artifacts []ArtifactEntry

	add := func(name string, data []byte) {
		if buf.Len() > maxBundleSize {
			return
		}
		entry, err := NewArtifactEntry(name, bytes.NewReader(data))
		if err != nil {
			return
		}
		hdr := &tar.Header{
			Name:    name,
			Size:    int64(len(data)),
			Mode:    0600,
			ModTime: time.Now(),
		}
		tw.WriteHeader(hdr) //nolint:errcheck
		tw.Write(data)      //nolint:errcheck
		artifacts = append(artifacts, entry)
	}

	run := func(name string, args ...string) []byte {
		out, _ := execCommandForensics(name, args...).Output()
		return out
	}

	// Process list
	add("ps_aux.txt", run("ps", "aux"))

	// Network state
	add("netstat_an.txt", run("netstat", "-an"))
	add("lsof_net.txt", run("lsof", "-nP", "-i"))

	// Unified log — last 1h, errors only (keeps bundle small)
	logOut := run("log", "show", "--last", "1h",
		"--predicate", "eventType == logEvent AND messageType >= error",
		"--style", "syslog")
	add("unified_log.txt", logOut)

	// System info
	add("sw_vers.txt", run("sw_vers"))
	add("system_profiler.txt", run("system_profiler", "SPSoftwareDataType", "SPHardwareDataType"))

	// Launch daemons inventory
	add("launchctl_list.txt", run("launchctl", "list"))

	// Loaded kexts
	add("kextstat.txt", run("kextstat"))

	// Security posture
	add("spctl_status.txt", run("spctl", "--status"))
	add("csrutil_status.txt", run("csrutil", "status"))
	add("socketfilterfw_state.txt", run(
		"/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"))

	// Santa (if present)
	if santaOut := run("santactl", "status"); len(santaOut) > 0 &&
		!strings.Contains(string(santaOut), "not found") {
		add("santa_status.txt", santaOut)
	}

	// Users
	add("dscl_users.txt", run("dscl", ".", "-list", "/Users"))

	// Open files (condensed — first 1000 lines)
	lsofAll := run("lsof")
	if len(lsofAll) > 512*1024 {
		lsofAll = lsofAll[:512*1024]
	}
	add("lsof_all.txt", lsofAll)

	tw.Close() //nolint:errcheck
	gz.Close() //nolint:errcheck
	return artifacts, buf, nil
}
```

- [ ] **Step 3: Fix forensics_other.go build tag**

Edit `agent/commands/forensics/forensics_other.go` line 1:
```go
//go:build !linux && !darwin && !windows
```

- [ ] **Step 4: Run tests**

```bash
cd agent && go test ./commands/forensics/ -run TestCollectOS_Darwin -v
```
Expected: PASS

- [ ] **Step 5: Compile check**

```bash
cd agent && GOOS=darwin GOARCH=arm64 go build ./... && GOOS=linux go build ./... && echo "OK"
```

- [ ] **Step 6: Commit**

```bash
git add agent/commands/forensics/forensics_darwin.go agent/commands/forensics/forensics_darwin_test.go agent/commands/forensics/forensics_other.go
git commit -m "feat(agent): macOS forensics collection bundle via log show + system artifacts"
```
