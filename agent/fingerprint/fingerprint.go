package fingerprint

// GetMachineID returns a stable identifier for this machine.
// Tries OS-native UUID first, falls back to MAC address hash.
func GetMachineID() (string, error) {
	id, err := nativeID()
	if err == nil && id != "" {
		return sanitize(id), nil
	}
	return macAddressHash()
}
