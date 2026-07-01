package tunnel

import (
	"context"
	"fmt"
	"log"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	pingInterval     = 20 * time.Second
	pongWait         = 30 * time.Second
	dialTimeout      = 10 * time.Second
	writeChannelSize = 256
	backoffMin       = 1 * time.Second
	backoffMax       = 30 * time.Second
)

// Run connects to the control plane tunnel endpoint and serves reverse-tunnel
// streams until ctx is cancelled. It reconnects with exponential backoff on
// any disconnection.
func Run(ctx context.Context, baseURL, secret, agentID string, allowlist []string) error {
	rules, err := ParseAllowlist(allowlist)
	if err != nil {
		return fmt.Errorf("invalid allowlist: %w", err)
	}

	wsURL := toWSURL(baseURL) + "/agent/tunnel?agent_id=" + agentID

	backoff := backoffMin
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		err := runOnce(ctx, wsURL, secret, rules)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err != nil {
			log.Printf("tunnel: disconnected (%v), reconnecting in %s", err, backoff)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}

		backoff *= 2
		if backoff > backoffMax {
			backoff = backoffMax
		}
	}
}

func toWSURL(base string) string {
	switch {
	case strings.HasPrefix(base, "https://"):
		return "wss://" + base[len("https://"):]
	case strings.HasPrefix(base, "http://"):
		return "ws://" + base[len("http://"):]
	default:
		return base
	}
}

// writeMsgBinary is sent to the writer goroutine.
type writeMsgBinary struct {
	data []byte
}

func runOnce(ctx context.Context, wsURL, secret string, rules []Rule) error {
	dialer := websocket.Dialer{}
	headers := http.Header{"Authorization": {"Bearer " + secret}}

	conn, _, err := dialer.DialContext(ctx, wsURL, headers)
	if err != nil {
		return fmt.Errorf("dial: %w", err)
	}
	defer conn.Close()

	// Writer goroutine — gorilla websocket is NOT safe for concurrent writes.
	writeCh := make(chan []byte, writeChannelSize)
	writerDone := make(chan struct{})
	go func() {
		defer close(writerDone)
		for data := range writeCh {
			if err := conn.WriteMessage(websocket.BinaryMessage, data); err != nil {
				return
			}
		}
	}()

	sendFrame := func(streamID uint32, typ byte, payload []byte) {
		select {
		case writeCh <- Encode(streamID, typ, payload):
		default:
			// channel full; drop (connection will be closed soon)
		}
	}

	// Pong handler + read deadline.
	conn.SetPongHandler(func(string) error {
		return conn.SetReadDeadline(time.Now().Add(pongWait))
	})
	_ = conn.SetReadDeadline(time.Now().Add(pongWait))

	// Heartbeat goroutine.
	heartbeatDone := make(chan struct{})
	go func() {
		defer close(heartbeatDone)
		ticker := time.NewTicker(pingInterval)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				deadline := time.Now().Add(10 * time.Second)
				if err := conn.WriteControl(websocket.PingMessage, nil, deadline); err != nil {
					return
				}
			case <-ctx.Done():
				return
			case <-writerDone:
				return
			}
		}
	}()

	// Active TCP streams: streamID -> net.Conn
	var mu sync.Mutex
	streams := make(map[uint32]net.Conn)

	closeStream := func(id uint32) {
		mu.Lock()
		c, ok := streams[id]
		delete(streams, id)
		mu.Unlock()
		if ok {
			c.Close()
		}
	}

	cleanup := func() {
		close(writeCh)
		<-writerDone
		mu.Lock()
		for id, c := range streams {
			c.Close()
			delete(streams, id)
		}
		mu.Unlock()
	}

	defer cleanup()

	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}

		_ = conn.SetReadDeadline(time.Now().Add(pongWait))
		mt, msg, err := conn.ReadMessage()
		if err != nil {
			return fmt.Errorf("read: %w", err)
		}
		if mt != websocket.BinaryMessage {
			continue
		}

		streamID, typ, payload, err := Decode(msg)
		if err != nil {
			log.Printf("tunnel: bad frame: %v", err)
			continue
		}

		switch typ {
		case OpOpen:
			host, port, err := ParseOpen(payload)
			if err != nil {
				sendFrame(streamID, OpOpenErr, []byte("invalid OPEN payload"))
				continue
			}
			if !IsAllowed(rules, host, port) {
				sendFrame(streamID, OpOpenErr, []byte("denied"))
				continue
			}
			addr := net.JoinHostPort(host, fmt.Sprintf("%d", port))
			go func(id uint32, address string) {
				tc, err := net.DialTimeout("tcp", address, dialTimeout)
				if err != nil {
					sendFrame(id, OpOpenErr, []byte(err.Error()))
					return
				}
				mu.Lock()
				streams[id] = tc
				mu.Unlock()
				sendFrame(id, OpOpenOK, nil)

				// Copy TCP -> WS as DATA frames.
				buf := make([]byte, 32*1024)
				for {
					n, err := tc.Read(buf)
					if n > 0 {
						frame := make([]byte, n)
						copy(frame, buf[:n])
						sendFrame(id, OpData, frame)
					}
					if err != nil {
						sendFrame(id, OpClose, nil)
						closeStream(id)
						return
					}
				}
			}(streamID, addr)

		case OpData:
			mu.Lock()
			tc, ok := streams[streamID]
			mu.Unlock()
			if !ok {
				continue
			}
			if _, err := tc.Write(payload); err != nil {
				closeStream(streamID)
				sendFrame(streamID, OpClose, nil)
			}

		case OpClose:
			closeStream(streamID)
		}
	}
}
