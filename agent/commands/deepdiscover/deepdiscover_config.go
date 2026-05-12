package deepdiscover

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

// parseConfigFiles parses a list of config file paths and returns ConfigEntry results.
// Files that are unreadable or of unknown type are silently skipped.
func parseConfigFiles(paths []string) []ConfigEntry {
	var entries []ConfigEntry
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		parsed := parseConfigFile(p, string(data))
		entries = append(entries, parsed...)
	}
	return entries
}

// parseConfigFile classifies and extracts metadata from a single config file.
func parseConfigFile(path, content string) []ConfigEntry {
	ext := strings.ToLower(filepath.Ext(path))
	base := strings.ToLower(filepath.Base(path))

	switch {
	case isApacheConfig(path, base):
		return parseApache(path, content)
	case isNginxConfig(path, base):
		return parseNginx(path, content)
	case isIISConfig(base):
		return parseIIS(path, content)
	case ext == ".conf" || ext == ".ini" || ext == ".env" || ext == ".cfg":
		return parseGeneric(path, content)
	default:
		return nil
	}
}

func isNginxConfig(path, base string) bool {
	return strings.Contains(path, "nginx") ||
		base == "nginx.conf" ||
		strings.Contains(path, "/sites-enabled/") ||
		strings.Contains(path, "/sites-available/") ||
		strings.Contains(path, "/conf.d/")
}

func isApacheConfig(path, base string) bool {
	return strings.Contains(path, "apache") ||
		strings.Contains(path, "httpd") ||
		(strings.HasSuffix(base, ".conf") && strings.Contains(path, "/apache"))
}

func isIISConfig(base string) bool {
	return base == "applicationhost.config" || base == "web.config"
}

// parseNginx extracts server_name, proxy_pass, and root directives.
func parseNginx(path, content string) []ConfigEntry {
	fields := map[string]string{}
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		for _, directive := range []string{"server_name", "proxy_pass", "root"} {
			if strings.HasPrefix(line, directive+" ") || strings.HasPrefix(line, directive+"\t") {
				val := strings.TrimPrefix(line, directive)
				val = strings.TrimSpace(val)
				val = strings.TrimSuffix(val, ";")
				parts := strings.Fields(val)
				if len(parts) > 0 {
					if _, exists := fields[directive]; !exists {
						fields[directive] = parts[0]
					}
				}
			}
		}
	}
	if len(fields) == 0 {
		return nil
	}
	return []ConfigEntry{{File: path, Type: "nginx", Fields: fields}}
}

// parseApache extracts ServerName, ProxyPass, and DocumentRoot directives.
func parseApache(path, content string) []ConfigEntry {
	fields := map[string]string{}
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		key := strings.ToLower(parts[0])
		switch key {
		case "servername":
			if _, exists := fields["server_name"]; !exists {
				fields["server_name"] = parts[1]
			}
		case "documentroot":
			if _, exists := fields["root"]; !exists {
				fields["root"] = parts[1]
			}
		case "proxypass":
			// ProxyPass <local-path> <url> — value is at index 2
			if len(parts) >= 3 {
				if _, exists := fields["proxy_pass"]; !exists {
					fields["proxy_pass"] = parts[2]
				}
			}
		}
	}
	if len(fields) == 0 {
		return nil
	}
	return []ConfigEntry{{File: path, Type: "apache", Fields: fields}}
}

// parseIIS extracts site binding hostnames from applicationHost.config.
func parseIIS(path, content string) []ConfigEntry {
	fields := map[string]string{}
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if !strings.Contains(line, "bindingInformation") {
			continue
		}
		const marker = `bindingInformation="`
		idx := strings.Index(line, marker)
		if idx < 0 {
			continue
		}
		rest := line[idx+len(marker):]
		end := strings.Index(rest, `"`)
		if end < 0 {
			continue
		}
		val := rest[:end]
		colonParts := strings.Split(val, ":")
		if len(colonParts) == 3 && colonParts[2] != "" {
			if _, exists := fields["server_name"]; !exists {
				fields["server_name"] = colonParts[2]
			}
		}
	}
	if len(fields) == 0 {
		return nil
	}
	return []ConfigEntry{{File: path, Type: "iis", Fields: fields}}
}

// parseGeneric reads key=value pairs from .conf/.ini/.env files.
// Values are intentionally discarded to avoid capturing secrets.
func parseGeneric(path, content string) []ConfigEntry {
	fields := map[string]string{}
	scanner := bufio.NewScanner(strings.NewReader(content))
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") || strings.HasPrefix(line, ";") {
			continue
		}
		idx := strings.IndexAny(line, "=:")
		if idx <= 0 {
			continue
		}
		key := strings.TrimSpace(line[:idx])
		if !isValidConfigKey(key) {
			continue
		}
		fields[key] = "" // value intentionally empty
	}
	if len(fields) == 0 {
		return nil
	}
	return []ConfigEntry{{File: path, Type: "generic", Fields: fields}}
}

func isValidConfigKey(s string) bool {
	if len(s) == 0 || len(s) > 128 {
		return false
	}
	for _, c := range s {
		if !((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
			(c >= '0' && c <= '9') || c == '_' || c == '-' || c == '.') {
			return false
		}
	}
	return true
}
