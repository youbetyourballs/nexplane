package registration

import (
	"context"
	"fmt"
	"net"
	"os"
	"runtime"
	"strings"

	"nexplane-agent/client"
)

type Info struct {
	AgentID         string
	AssetID         string
	TunnelEnabled   bool
	TunnelAllowlist []string
}

// Register sends the registration payload to the control plane.
func Register(ctx context.Context, c *client.Client, machineID, hostname, osType, agentVersion string) (*Info, error) {
	ips := getIPAddresses()
	osVersion := getOSVersion()

	resp, err := c.Register(ctx, client.RegisterRequest{
		MachineID:    machineID,
		Hostname:     hostname,
		OsType:       osType,
		IPAddresses:  ips,
		OsVersion:    osVersion,
		AgentVersion: agentVersion,
	})
	if err != nil {
		return nil, fmt.Errorf("registering agent: %w", err)
	}
	return &Info{
		AgentID:         resp.AgentID,
		AssetID:         resp.AssetID,
		TunnelEnabled:   resp.TunnelEnabled,
		TunnelAllowlist: resp.TunnelAllowlist,
	}, nil
}

func getIPAddresses() []string {
	var ips []string
	ifaces, err := net.Interfaces()
	if err != nil {
		return ips
	}
	for _, iface := range ifaces {
		if iface.Flags&net.FlagLoopback != 0 {
			continue
		}
		addrs, _ := iface.Addrs()
		for _, addr := range addrs {
			if ipnet, ok := addr.(*net.IPNet); ok {
				ips = append(ips, ipnet.IP.String())
			}
		}
	}
	return ips
}

func getOSVersion() string {
	if runtime.GOOS == "linux" {
		data, err := os.ReadFile("/etc/os-release")
		if err == nil {
			for _, line := range strings.Split(string(data), "\n") {
				if strings.HasPrefix(line, "PRETTY_NAME=") {
					return strings.Trim(line[12:], `"`)
				}
			}
		}
	}
	return runtime.GOOS + "/" + runtime.GOARCH
}
