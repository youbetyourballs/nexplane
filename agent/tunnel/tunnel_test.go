package tunnel

import (
	"context"
	"encoding/hex"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// ---- Frame encode/decode tests ----

// TestFrameGoldenVectors locks the wire format byte-for-byte against the Python
// relay codec (backend/app/tunnel/protocol.py). The SAME hex vectors are
// asserted in backend/app/tests/test_tunnel_protocol_authorizer.py — if either
// side changes the layout, one of the two conformance tests fails.
func TestFrameGoldenVectors(t *testing.T) {
	cases := []struct {
		streamID uint32
		typ      byte
		payload  string
		wantHex  string
	}{
		{0x01020304, OpData, "hi", "01020304046869"},
		{1, OpOpen, "db.internal:5432", "000000010164622e696e7465726e616c3a35343332"},
		{7, OpOpenOK, "", "0000000702"},
		{255, OpClose, "", "000000ff05"},
	}
	for _, c := range cases {
		got := hex.EncodeToString(Encode(c.streamID, c.typ, []byte(c.payload)))
		if got != c.wantHex {
			t.Errorf("Encode(%d,%d,%q) = %s, want %s", c.streamID, c.typ, c.payload, got, c.wantHex)
		}
	}
}

func TestEncodeDecodeRoundtrip(t *testing.T) {
	cases := []struct {
		name     string
		streamID uint32
		typ      byte
		payload  []byte
	}{
		{"empty payload", 1, OpOpen, nil},
		{"open ok", 42, OpOpenOK, []byte{}},
		{"data", 0xDEADBEEF, OpData, []byte("hello world")},
		{"close", 7, OpClose, nil},
		{"open err", 3, OpOpenErr, []byte("denied")},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := Encode(tc.streamID, tc.typ, tc.payload)
			sid, typ, payload, err := Decode(b)
			if err != nil {
				t.Fatalf("Decode error: %v", err)
			}
			if sid != tc.streamID {
				t.Errorf("streamID: got %d want %d", sid, tc.streamID)
			}
			if typ != tc.typ {
				t.Errorf("type: got %d want %d", typ, tc.typ)
			}
			if string(payload) != string(tc.payload) {
				t.Errorf("payload: got %q want %q", payload, tc.payload)
			}
		})
	}
}

func TestDecodeTooShort(t *testing.T) {
	_, _, _, err := Decode([]byte{1, 2, 3})
	if err == nil {
		t.Fatal("expected error for short frame")
	}
}

// ---- ParseOpen tests ----

func TestParseOpen(t *testing.T) {
	cases := []struct {
		input   string
		host    string
		port    int
		wantErr bool
	}{
		{"localhost:8080", "localhost", 8080, false},
		{"192.168.1.1:443", "192.168.1.1", 443, false},
		{"[::1]:9000", "[::1]", 9000, false},
		{"2001:db8::1:22", "2001:db8::1", 22, false},
		{"noport", "", 0, true},
		{"host:notaport", "", 0, true},
		{"host:0", "", 0, true},
	}
	for _, tc := range cases {
		t.Run(tc.input, func(t *testing.T) {
			host, port, err := ParseOpen([]byte(tc.input))
			if tc.wantErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if host != tc.host {
				t.Errorf("host: got %q want %q", host, tc.host)
			}
			if port != tc.port {
				t.Errorf("port: got %d want %d", port, tc.port)
			}
		})
	}
}

// ---- Allowlist tests ----

func TestAllowlist(t *testing.T) {
	cases := []struct {
		name    string
		rules   []string
		host    string
		port    int
		allowed bool
	}{
		{"cidr match", []string{"192.168.0.0/24:80"}, "192.168.0.5", 80, true},
		{"cidr no match port", []string{"192.168.0.0/24:80"}, "192.168.0.5", 443, false},
		{"cidr no match host", []string{"192.168.0.0/24:80"}, "10.0.0.1", 80, false},
		{"port range allow", []string{"10.0.0.1:8000-9000"}, "10.0.0.1", 8500, true},
		{"port range deny low", []string{"10.0.0.1:8000-9000"}, "10.0.0.1", 7999, false},
		{"port range deny high", []string{"10.0.0.1:8000-9000"}, "10.0.0.1", 9001, false},
		{"wildcard port", []string{"10.0.0.1:*"}, "10.0.0.1", 12345, true},
		{"hostname exact", []string{"example.com:443"}, "example.com", 443, true},
		{"hostname case insensitive", []string{"Example.COM:443"}, "example.com", 443, true},
		{"hostname no match", []string{"example.com:443"}, "other.com", 443, false},
		{"deny by default (empty)", []string{}, "127.0.0.1", 80, false},
		{"bare IP match", []string{"127.0.0.1:22"}, "127.0.0.1", 22, true},
		{"bare IP port mismatch", []string{"127.0.0.1:22"}, "127.0.0.1", 80, false},
		{"hostname rule does not match IP", []string{"localhost:80"}, "127.0.0.1", 80, false},
		{"cidr rule does not match hostname", []string{"192.168.0.0/24:80"}, "myhost", 80, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rules, err := ParseAllowlist(tc.rules)
			if err != nil {
				t.Fatalf("ParseAllowlist error: %v", err)
			}
			got := IsAllowed(rules, tc.host, tc.port)
			if got != tc.allowed {
				t.Errorf("IsAllowed(%q, %d) = %v, want %v", tc.host, tc.port, got, tc.allowed)
			}
		})
	}
}

// ---- Integration test ----

var upgrader = websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return true }}

// echoServer starts a TCP echo server, returns listener and addr.
func echoServer(t *testing.T) (net.Listener, string) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("echo listen: %v", err)
	}
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go io.Copy(c, c)
		}
	}()
	return ln, ln.Addr().String()
}

// cpServer starts a fake control-plane WebSocket server.
// It runs handler in a goroutine for each connection.
func cpServer(t *testing.T, handler func(*websocket.Conn)) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ws, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			t.Logf("upgrade error: %v", err)
			return
		}
		defer ws.Close()
		handler(ws)
	}))
}

func TestIntegration_EchoAndDeny(t *testing.T) {
	echoLn, echoAddr := echoServer(t)
	defer echoLn.Close()

	// Parse echo addr host:port
	echoHost, echoPort, err := net.SplitHostPort(echoAddr)
	if err != nil {
		t.Fatalf("split echo addr: %v", err)
	}

	// Mutex + channel to collect frames received by cp from agent.
	var cpMu sync.Mutex
	cpFrames := make([]struct {
		sid     uint32
		typ     byte
		payload []byte
	}, 0)
	gotFrame := make(chan struct{}, 32)

	recordFrame := func(sid uint32, typ byte, payload []byte) {
		cpMu.Lock()
		cpFrames = append(cpFrames, struct {
			sid     uint32
			typ     byte
			payload []byte
		}{sid, typ, payload})
		cpMu.Unlock()
		select {
		case gotFrame <- struct{}{}:
		default:
		}
	}

	const echoStreamID = uint32(1)
	const deniedStreamID = uint32(2)

	var cpWS *websocket.Conn
	var cpWSMu sync.Mutex
	serverReady := make(chan struct{})

	srv := cpServer(t, func(ws *websocket.Conn) {
		cpWSMu.Lock()
		cpWS = ws
		cpWSMu.Unlock()
		close(serverReady)

		for {
			mt, msg, err := ws.ReadMessage()
			if err != nil {
				return
			}
			if mt != websocket.BinaryMessage {
				continue
			}
			sid, typ, payload, err := Decode(msg)
			if err != nil {
				continue
			}
			p := make([]byte, len(payload))
			copy(p, payload)
			recordFrame(sid, typ, p)
		}
	})
	defer srv.Close()

	// allowlist: only the echo server addr
	allowlist := []string{echoHost + ":" + echoPort}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// Run agent in background
	runErr := make(chan error, 1)
	go func() {
		runErr <- Run(ctx, strings.Replace(srv.URL, "http://", "http://", 1), "test-secret", "agent-1", allowlist)
	}()

	// Wait for server to receive agent connection
	select {
	case <-serverReady:
	case <-time.After(5 * time.Second):
		t.Fatal("server never got connection")
	}

	time.Sleep(100 * time.Millisecond) // give agent goroutines a moment

	cpWSMu.Lock()
	ws := cpWS
	cpWSMu.Unlock()

	sendCP := func(sid uint32, typ byte, payload []byte) {
		if err := ws.WriteMessage(websocket.BinaryMessage, Encode(sid, typ, payload)); err != nil {
			t.Logf("cp write error: %v", err)
		}
	}

	// Send OPEN for echo server (should be allowed)
	sendCP(echoStreamID, OpOpen, []byte(echoAddr))

	// Wait for OPEN_OK
	waitFor := func(t *testing.T, sid uint32, typ byte, timeout time.Duration) []byte {
		t.Helper()
		deadline := time.Now().Add(timeout)
		for time.Now().Before(deadline) {
			cpMu.Lock()
			for _, f := range cpFrames {
				if f.sid == sid && f.typ == typ {
					cpMu.Unlock()
					return f.payload
				}
			}
			cpMu.Unlock()
			select {
			case <-gotFrame:
			case <-time.After(100 * time.Millisecond):
			}
		}
		t.Fatalf("timed out waiting for streamID=%d type=%d", sid, typ)
		return nil
	}

	waitFor(t, echoStreamID, OpOpenOK, 5*time.Second)

	// Send DATA
	testData := []byte("ping!")
	sendCP(echoStreamID, OpData, testData)

	// Wait for echoed DATA back
	deadline := time.Now().Add(5 * time.Second)
	var gotData []byte
	for time.Now().Before(deadline) {
		cpMu.Lock()
		for _, f := range cpFrames {
			if f.sid == echoStreamID && f.typ == OpData {
				gotData = f.payload
			}
		}
		cpMu.Unlock()
		if gotData != nil {
			break
		}
		select {
		case <-gotFrame:
		case <-time.After(50 * time.Millisecond):
		}
	}
	if string(gotData) != string(testData) {
		t.Errorf("echo data: got %q want %q", gotData, testData)
	}

	// Send OPEN for a non-allowlisted address (denied)
	sendCP(deniedStreamID, OpOpen, []byte("192.168.99.99:1234"))
	errPayload := waitFor(t, deniedStreamID, OpOpenErr, 3*time.Second)
	if string(errPayload) != "denied" {
		t.Errorf("expected 'denied', got %q", errPayload)
	}

	cancel()
}
