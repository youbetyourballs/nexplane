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

var execCommandForensics = exec.Command

const maxBundleSize = 2 * 1024 * 1024 * 1024 // 2 GB

func collectOS(_ context.Context, cfg ForensicsConfig) ([]ArtifactEntry, bytes.Buffer, error) {
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

	add("ps_aux.txt", run("ps", "aux"))
	add("netstat_an.txt", run("netstat", "-an"))
	add("lsof_net.txt", run("lsof", "-nP", "-i"))
	logOut := run("log", "show", "--last", "1h",
		"--predicate", "eventType == logEvent AND messageType >= error",
		"--style", "syslog")
	add("unified_log.txt", logOut)
	add("sw_vers.txt", run("sw_vers"))
	add("system_profiler.txt", run("system_profiler", "SPSoftwareDataType", "SPHardwareDataType"))
	add("launchctl_list.txt", run("launchctl", "list"))
	add("kextstat.txt", run("kextstat"))
	add("spctl_status.txt", run("spctl", "--status"))
	add("csrutil_status.txt", run("csrutil", "status"))
	add("socketfilterfw_state.txt", run(
		"/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"))

	if santaOut := run("santactl", "status"); len(santaOut) > 0 &&
		!strings.Contains(string(santaOut), "not found") {
		add("santa_status.txt", santaOut)
	}

	add("dscl_users.txt", run("dscl", ".", "-list", "/Users"))

	lsofAll := run("lsof")
	if len(lsofAll) > 512*1024 {
		lsofAll = lsofAll[:512*1024]
	}
	add("lsof_all.txt", lsofAll)

	_ = cfg // IncludeMemoryDump deferred for macOS (no /proc equivalent)

	tw.Close() //nolint:errcheck
	gz.Close() //nolint:errcheck
	return artifacts, buf, nil
}
