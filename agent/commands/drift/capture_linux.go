//go:build linux

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package drift

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

func captureDriftState(params map[string]any) (map[string]any, error) {
	surfaceType, _ := params["surface_type"].(string)

	var state map[string]any
	var err error

	switch surfaceType {
	case "ssh_config":
		state, err = captureSSHConfig()
	case "sudoers":
		state, err = captureSudoers()
	case "cron_jobs":
		state, err = captureCronJobs()
	case "listening_ports":
		state, err = captureListeningPorts()
	case "running_services":
		state, err = captureRunningServices()
	case "users_groups":
		state, err = captureUsersGroups()
	case "firewall_rules":
		state, err = captureFirewallRules()
	default:
		return nil, fmt.Errorf("unsupported surface_type: %s", surfaceType)
	}

	if err != nil {
		return map[string]any{"status": "failed", "error": err.Error()}, nil
	}
	return map[string]any{"status": "success", "state": state}, nil
}

func captureSSHConfig() (map[string]any, error) {
	data, err := os.ReadFile("/etc/ssh/sshd_config")
	if err != nil {
		return nil, fmt.Errorf("reading sshd_config: %w", err)
	}
	result := map[string]any{}
	scanner := bufio.NewScanner(strings.NewReader(string(data)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, " ", 2)
		if len(parts) == 2 {
			result[parts[0]] = strings.TrimSpace(parts[1])
		}
	}
	return result, nil
}

func captureSudoers() (map[string]any, error) {
	entries := []string{}

	readFile := func(path string) {
		data, err := os.ReadFile(path)
		if err != nil {
			return
		}
		scanner := bufio.NewScanner(strings.NewReader(string(data)))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, "Defaults") {
				continue
			}
			entries = append(entries, line)
		}
	}

	readFile("/etc/sudoers")

	dropDFiles, _ := os.ReadDir("/etc/sudoers.d")
	dropDNames := []string{}
	for _, f := range dropDFiles {
		if !f.IsDir() {
			dropDNames = append(dropDNames, "/etc/sudoers.d/"+f.Name())
		}
	}
	sort.Strings(dropDNames)
	for _, fp := range dropDNames {
		readFile(fp)
	}

	sort.Strings(entries)
	return map[string]any{"entries": entries}, nil
}

func captureCronJobs() (map[string]any, error) {
	entries := []string{}

	readFile := func(path string) {
		data, err := os.ReadFile(path)
		if err != nil {
			return
		}
		scanner := bufio.NewScanner(strings.NewReader(string(data)))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line != "" && !strings.HasPrefix(line, "#") {
				entries = append(entries, line)
			}
		}
	}

	readFile("/etc/crontab")

	sysDirs := []string{"/etc/cron.d", "/etc/cron.daily", "/etc/cron.hourly", "/etc/cron.weekly", "/etc/cron.monthly"}
	for _, dir := range sysDirs {
		files, _ := os.ReadDir(dir)
		names := []string{}
		for _, f := range files {
			if !f.IsDir() {
				names = append(names, dir+"/"+f.Name())
			}
		}
		sort.Strings(names)
		for _, fp := range names {
			readFile(fp)
		}
	}

	sort.Strings(entries)
	return map[string]any{"entries": entries}, nil
}

func captureListeningPorts() (map[string]any, error) {
	out, err := exec.Command("ss", "-tlnpu").Output()
	if err != nil {
		return nil, fmt.Errorf("ss -tlnpu: %w", err)
	}

	reAddr := regexp.MustCompile(`(\[?[0-9a-f:.]+\]?|\*):(\d+)$`)
	rePid := regexp.MustCompile(`pid=(\d+)`)
	reProc := regexp.MustCompile(`"([^"]+)"`)

	ports := []map[string]any{}
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := scanner.Text()
		if strings.HasPrefix(line, "Netid") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 5 {
			continue
		}
		proto := fields[0]
		localAddr := fields[4]

		m := reAddr.FindStringSubmatch(localAddr)
		if m == nil {
			continue
		}
		bind := strings.Trim(m[1], "[]")
		portNum, _ := strconv.Atoi(m[2])

		entry := map[string]any{
			"proto": proto,
			"port":  portNum,
			"bind":  bind,
		}
		if len(fields) > 5 {
			processField := strings.Join(fields[5:], " ")
			if pidM := rePid.FindStringSubmatch(processField); pidM != nil {
				pid, _ := strconv.Atoi(pidM[1])
				entry["pid"] = pid
			}
			if procM := reProc.FindStringSubmatch(processField); procM != nil {
				entry["process"] = procM[1]
			}
		}
		ports = append(ports, entry)
	}

	sort.Slice(ports, func(i, j int) bool {
		pi, _ := ports[i]["port"].(int)
		pj, _ := ports[j]["port"].(int)
		return pi < pj
	})

	return map[string]any{"ports": ports}, nil
}

func captureRunningServices() (map[string]any, error) {
	out, err := exec.Command("systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager").Output()
	if err != nil {
		return nil, fmt.Errorf("systemctl list-units: %w", err)
	}

	services := []string{}
	scanner := bufio.NewScanner(strings.NewReader(string(out)))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) > 0 {
			services = append(services, fields[0])
		}
	}
	sort.Strings(services)
	return map[string]any{"services": services}, nil
}

func captureUsersGroups() (map[string]any, error) {
	users := []map[string]any{}
	if data, err := os.ReadFile("/etc/passwd"); err == nil {
		scanner := bufio.NewScanner(strings.NewReader(string(data)))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			parts := strings.Split(line, ":")
			if len(parts) < 7 {
				continue
			}
			uid, _ := strconv.Atoi(parts[2])
			gid, _ := strconv.Atoi(parts[3])
			users = append(users, map[string]any{
				"username": parts[0],
				"uid":      uid,
				"gid":      gid,
				"home":     parts[5],
				"shell":    parts[6],
			})
		}
	}
	sort.Slice(users, func(i, j int) bool {
		a, _ := users[i]["username"].(string)
		b, _ := users[j]["username"].(string)
		return a < b
	})

	groups := []map[string]any{}
	if data, err := os.ReadFile("/etc/group"); err == nil {
		scanner := bufio.NewScanner(strings.NewReader(string(data)))
		for scanner.Scan() {
			line := strings.TrimSpace(scanner.Text())
			if line == "" || strings.HasPrefix(line, "#") {
				continue
			}
			parts := strings.Split(line, ":")
			if len(parts) < 4 {
				continue
			}
			gid, _ := strconv.Atoi(parts[2])
			members := []string{}
			if parts[3] != "" {
				members = strings.Split(parts[3], ",")
				sort.Strings(members)
			}
			groups = append(groups, map[string]any{
				"name":    parts[0],
				"gid":     gid,
				"members": members,
			})
		}
	}
	sort.Slice(groups, func(i, j int) bool {
		a, _ := groups[i]["name"].(string)
		b, _ := groups[j]["name"].(string)
		return a < b
	})

	return map[string]any{"users": users, "groups": groups}, nil
}

func captureFirewallRules() (map[string]any, error) {
	if _, err := exec.LookPath("nft"); err == nil {
		out, err := exec.Command("nft", "list", "ruleset").Output()
		if err == nil {
			return map[string]any{"tool": "nftables", "rules": string(out)}, nil
		}
	}
	if _, err := exec.LookPath("iptables-save"); err == nil {
		out, err := exec.Command("iptables-save").Output()
		if err == nil {
			return map[string]any{"tool": "iptables", "rules": string(out)}, nil
		}
	}
	return map[string]any{"tool": "none", "rules": ""}, nil
}
