package tunnel

import (
	"encoding/binary"
	"fmt"
	"strconv"
	"strings"
)

// Frame type constants.
const (
	OpOpen    byte = 1
	OpOpenOK  byte = 2
	OpOpenErr byte = 3
	OpData    byte = 4
	OpClose   byte = 5
)

// Encode builds a wire frame: [streamID 4B][type 1B][payload].
func Encode(streamID uint32, typ byte, payload []byte) []byte {
	buf := make([]byte, 5+len(payload))
	binary.BigEndian.PutUint32(buf[0:4], streamID)
	buf[4] = typ
	copy(buf[5:], payload)
	return buf
}

// Decode parses a wire frame.
func Decode(b []byte) (streamID uint32, typ byte, payload []byte, err error) {
	if len(b) < 5 {
		return 0, 0, nil, fmt.Errorf("frame too short: %d bytes", len(b))
	}
	streamID = binary.BigEndian.Uint32(b[0:4])
	typ = b[4]
	payload = b[5:]
	return streamID, typ, payload, nil
}

// ParseOpen parses an OPEN payload "host:port" splitting on the last ":".
func ParseOpen(payload []byte) (host string, port int, err error) {
	s := string(payload)
	idx := strings.LastIndex(s, ":")
	if idx < 0 {
		return "", 0, fmt.Errorf("no colon in OPEN payload: %q", s)
	}
	host = s[:idx]
	portStr := s[idx+1:]
	p, e := strconv.Atoi(portStr)
	if e != nil || p < 1 || p > 65535 {
		return "", 0, fmt.Errorf("invalid port %q", portStr)
	}
	return host, p, nil
}
