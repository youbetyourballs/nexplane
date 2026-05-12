package deepdiscover

import (
	"testing"
)

func TestParseNginxConfig(t *testing.T) {
	input := `
server {
    server_name payments.internal;
    location / {
        proxy_pass http://10.0.1.45:8080;
        root /var/www/html;
    }
}`
	entries := parseConfigFile("/etc/nginx/nginx.conf", input)
	if len(entries) == 0 {
		t.Fatal("expected at least one entry")
	}
	e := entries[0]
	if e.Type != "nginx" {
		t.Errorf("expected type=nginx, got %q", e.Type)
	}
	if e.Fields["server_name"] != "payments.internal" {
		t.Errorf("expected server_name=payments.internal, got %q", e.Fields["server_name"])
	}
	if e.Fields["proxy_pass"] != "http://10.0.1.45:8080" {
		t.Errorf("expected proxy_pass=http://10.0.1.45:8080, got %q", e.Fields["proxy_pass"])
	}
}

func TestParseApacheConfig(t *testing.T) {
	input := `<VirtualHost *:80>
    ServerName app.internal
    ProxyPass / http://127.0.0.1:3000/
    DocumentRoot /var/www
</VirtualHost>`
	entries := parseConfigFile("/etc/apache2/sites-enabled/app.conf", input)
	if len(entries) == 0 {
		t.Fatal("expected at least one entry")
	}
	e := entries[0]
	if e.Type != "apache" {
		t.Errorf("expected type=apache, got %q", e.Type)
	}
	if e.Fields["server_name"] != "app.internal" {
		t.Errorf("expected server_name=app.internal, got %q", e.Fields["server_name"])
	}
	if e.Fields["proxy_pass"] != "http://127.0.0.1:3000/" {
		t.Errorf("expected proxy_pass=http://127.0.0.1:3000/, got %q", e.Fields["proxy_pass"])
	}
}

func TestParseGenericConfig(t *testing.T) {
	input := `DB_HOST=10.0.0.1
APP_PORT=8080
SECRET_KEY=do_not_capture_this`
	entries := parseConfigFile("/opt/myapp/.env", input)
	if len(entries) == 0 {
		t.Fatal("expected at least one entry")
	}
	e := entries[0]
	if e.Type != "generic" {
		t.Errorf("expected type=generic, got %q", e.Type)
	}
	// Keys should be present, values should NOT be captured
	if _, ok := e.Fields["DB_HOST"]; !ok {
		t.Error("expected DB_HOST key to be present")
	}
	if e.Fields["DB_HOST"] != "" {
		t.Errorf("expected empty value (no secret capture), got %q", e.Fields["DB_HOST"])
	}
	if _, ok := e.Fields["SECRET_KEY"]; !ok {
		t.Error("expected SECRET_KEY key present")
	}
	if e.Fields["SECRET_KEY"] != "" {
		t.Error("must not capture secret values in generic config")
	}
}

func TestParseConfigFileUnknownReturnsEmpty(t *testing.T) {
	entries := parseConfigFile("/etc/fstab", "UUID=abc /boot ext4 defaults 0 2")
	if len(entries) != 0 {
		t.Errorf("expected empty for unknown config type, got %d entries", len(entries))
	}
}
