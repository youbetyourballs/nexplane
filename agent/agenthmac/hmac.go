package agenthmac

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/json"
	"fmt"
)

// Sign computes HMAC-SHA256 of "{jobID}:{command}:{canonicalJSON(params)}".
// json.Marshal on map[string]any sorts keys alphabetically — this is the canonical form.
func Sign(secret, jobID, command string, params map[string]any) string {
	canonical := mustMarshal(params)
	message := fmt.Sprintf("%s:%s:%s", jobID, command, canonical)
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(message))
	return fmt.Sprintf("%x", mac.Sum(nil))
}

// Verify checks a signature using constant-time comparison.
func Verify(secret, jobID, command string, params map[string]any, signature string) bool {
	expected := Sign(secret, jobID, command, params)
	return hmac.Equal([]byte(expected), []byte(signature))
}

func mustMarshal(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(fmt.Sprintf("agenthmac: cannot marshal params: %v", err))
	}
	return string(b)
}
