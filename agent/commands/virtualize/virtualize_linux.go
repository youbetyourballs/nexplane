//go:build linux

package virtualize

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

func executeOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	targetMode, _ := params["target_mode"].(string)

	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required")
	}
	if targetMode == "" {
		return nil, fmt.Errorf("target_mode is required")
	}

	sourceDevice, _ := params["source_device"].(string)
	if sourceDevice == "" {
		var err error
		sourceDevice, err = detectRootDevice()
		if err != nil {
			return nil, fmt.Errorf("detecting root device: %w", err)
		}
	}

	if err := runVCmd("dd", "if="+sourceDevice, "of="+imagePath, "bs=4M", "conv=fsync"); err != nil {
		return nil, fmt.Errorf("dd failed: %w", err)
	}

	loopOut, err := exec.Command("losetup", "--find", "--show", "--partscan", imagePath).Output()
	if err != nil {
		return nil, fmt.Errorf("losetup failed: %w", err)
	}
	loopDev := strings.TrimSpace(string(loopOut))

	mountPoint := filepath.Join(os.TempDir(), "nexplane-mount-"+filepath.Base(imagePath))
	if err := os.MkdirAll(mountPoint, 0755); err != nil {
		return nil, fmt.Errorf("creating mount point: %w", err)
	}

	rootPart := findRootPartition(loopDev)
	if err := runVCmd("mount", rootPart, mountPoint); err != nil {
		exec.Command("losetup", "-d", loopDev).Run()
		return nil, fmt.Errorf("mounting partition: %w", err)
	}

	if err := applyNetConfigInMount(mountPoint, params); err != nil {
		exec.Command("umount", mountPoint).Run()
		exec.Command("losetup", "-d", loopDev).Run()
		return nil, fmt.Errorf("applying network config in image: %w", err)
	}

	exec.Command("umount", mountPoint).Run()
	exec.Command("losetup", "-d", loopDev).Run()
	os.Remove(mountPoint)

	info, _ := os.Stat(imagePath)
	imageSize := int64(0)
	if info != nil {
		imageSize = info.Size()
	}

	return map[string]any{
		"action":                 "virtualize_for_migration",
		"image_path":             imagePath,
		"image_size_bytes":       imageSize,
		"source_device":          sourceDevice,
		"network_config_applied": true,
		"completed_at":           time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func rollbackOS(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required for rollback")
	}
	if err := os.Remove(imagePath); err != nil && !os.IsNotExist(err) {
		return nil, fmt.Errorf("deleting image: %w", err)
	}
	return map[string]any{"rolled_back": true, "deleted": true, "image_path": imagePath}, nil
}

func detectRootDevice() (string, error) {
	out, err := exec.Command("findmnt", "-n", "-o", "SOURCE", "/").Output()
	if err != nil {
		return "", err
	}
	src := strings.TrimSpace(string(out))
	for i := len(src) - 1; i >= 0; i-- {
		if src[i] < '0' || src[i] > '9' {
			return src[:i+1], nil
		}
	}
	return src, nil
}

func findRootPartition(loopDev string) string {
	for i := 1; i <= 4; i++ {
		part := fmt.Sprintf("%sp%d", loopDev, i)
		if _, err := os.Stat(part); err == nil {
			return part
		}
	}
	return loopDev
}

func applyNetConfigInMount(mountPoint string, params map[string]any) error {
	targetMode, _ := params["target_mode"].(string)
	iface, _ := params["target_interface"].(string)
	if iface == "" {
		iface = "eth0"
	}

	nmPath := filepath.Join(mountPoint, "etc", "NetworkManager", "system-connections")
	if _, err := os.Stat(nmPath); err == nil {
		return writeNMConnection(filepath.Join(nmPath, iface+".nmconnection"), iface, targetMode, params)
	}
	ifacesPath := filepath.Join(mountPoint, "etc", "network", "interfaces")
	if _, err := os.Stat(ifacesPath); err == nil {
		return writeDebianInterfaces(ifacesPath, iface, targetMode, params)
	}
	return writeRHELIfcfg(filepath.Join(mountPoint, "etc", "sysconfig", "network-scripts", "ifcfg-"+iface), iface, targetMode, params)
}

func writeNMConnection(path, iface, mode string, params map[string]any) error {
	content := fmt.Sprintf("[connection]\nid=%s\ntype=ethernet\ninterface-name=%s\n\n[ethernet]\n\n[ipv4]\n", iface, iface)
	if mode == "dhcp" {
		content += "method=auto\n"
	} else {
		v4, _ := params["target_ip_v4"].(string)
		gw, _ := params["target_gateway_v4"].(string)
		content += fmt.Sprintf("method=manual\naddress1=%s,%s\n", v4, gw)
	}
	content += "\n[ipv6]\nmethod=auto\n"
	return os.WriteFile(path, []byte(content), 0600)
}

func writeDebianInterfaces(path, iface, mode string, params map[string]any) error {
	content := fmt.Sprintf("auto lo\niface lo inet loopback\n\nauto %s\n", iface)
	if mode == "dhcp" {
		content += fmt.Sprintf("iface %s inet dhcp\n", iface)
	} else {
		v4, _ := params["target_ip_v4"].(string)
		parts := strings.SplitN(v4, "/", 2)
		gw, _ := params["target_gateway_v4"].(string)
		content += fmt.Sprintf("iface %s inet static\n  address %s\n  gateway %s\n", iface, parts[0], gw)
	}
	return os.WriteFile(path, []byte(content), 0644)
}

func writeRHELIfcfg(path, iface, mode string, params map[string]any) error {
	content := fmt.Sprintf("DEVICE=%s\nONBOOT=yes\n", iface)
	if mode == "dhcp" {
		content += "BOOTPROTO=dhcp\n"
	} else {
		v4, _ := params["target_ip_v4"].(string)
		parts := strings.SplitN(v4, "/", 2)
		gw, _ := params["target_gateway_v4"].(string)
		content += fmt.Sprintf("BOOTPROTO=static\nIPADDR=%s\nGATEWAY=%s\n", parts[0], gw)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(content), 0644)
}

func runVCmd(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %v: %w (output: %s)", name, args, err, out)
	}
	return nil
}
