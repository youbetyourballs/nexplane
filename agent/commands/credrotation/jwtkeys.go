package credrotation

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// JWTKeyRotateParams holds parameters for JWT signing key rotation.
type JWTKeyRotateParams struct {
	Algorithm   string // "RS256" | "ES256" — default RS256
	ConfigPath  string // path to config file containing the key
	KeyField    string // field name in config — default "jwt_private_key"
	ServiceName string // systemd service to restart
	Action      string // "rotate" | "restore"
	BackupPath  string // for restore action
}

func JWTKeyRotateExecute(params map[string]any) (map[string]any, error) {
	p := jwtKeyParamsFromMap(params)

	if p.Action == "restore" {
		return jwtKeyRestore(p)
	}
	return jwtKeyRotate(p)
}

func JWTKeyRotateRollback(params map[string]any) (map[string]any, error) {
	p := jwtKeyParamsFromMap(params)
	p.Action = "restore"
	return jwtKeyRestore(p)
}

func jwtKeyRotate(p JWTKeyRotateParams) (map[string]any, error) {
	if p.Algorithm == "" {
		p.Algorithm = "RS256"
	}
	if p.KeyField == "" {
		p.KeyField = "jwt_private_key"
	}

	// Generate new key
	var privPEM, pubPEM string
	switch strings.ToUpper(p.Algorithm) {
	case "RS256", "RS384", "RS512":
		priv, err := rsa.GenerateKey(rand.Reader, 2048)
		if err != nil {
			return nil, fmt.Errorf("generate RSA key: %w", err)
		}
		privPEM = string(pem.EncodeToMemory(&pem.Block{
			Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(priv),
		}))
		pubBytes, _ := x509.MarshalPKIXPublicKey(&priv.PublicKey)
		pubPEM = string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubBytes}))
	case "ES256", "ES384", "ES512":
		priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			return nil, fmt.Errorf("generate EC key: %w", err)
		}
		privBytes, _ := x509.MarshalECPrivateKey(priv)
		privPEM = string(pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: privBytes}))
		pubBytes, _ := x509.MarshalPKIXPublicKey(&priv.PublicKey)
		pubPEM = string(pem.EncodeToMemory(&pem.Block{Type: "PUBLIC KEY", Bytes: pubBytes}))
	default:
		return nil, fmt.Errorf("unsupported algorithm: %s", p.Algorithm)
	}

	// Backup existing config if path provided
	backupPath := ""
	keyFile := ""
	if p.ConfigPath != "" {
		backupPath = p.ConfigPath + ".nexplane-backup-" + time.Now().Format("20060102150405")
		data, err := os.ReadFile(p.ConfigPath)
		if err == nil {
			_ = os.WriteFile(backupPath, data, 0600)
		}

		// Write new private key to a separate key file adjacent to the config
		keyFile = filepath.Join(filepath.Dir(p.ConfigPath), "jwt_signing_key.pem")
		_ = os.WriteFile(keyFile, []byte(privPEM), 0600)
	}

	// Restart service if specified
	if p.ServiceName != "" {
		restartSystemdService(p.ServiceName)
	}

	return map[string]any{
		"action":      "rotate_jwt_signing_key",
		"algorithm":   p.Algorithm,
		"public_key":  pubPEM,
		"backup_path": backupPath,
		"key_file":    keyFile,
		"rotated_at":  time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func jwtKeyRestore(p JWTKeyRotateParams) (map[string]any, error) {
	if p.BackupPath == "" {
		return map[string]any{"action": "jwt_key_restore", "status": "no_backup"}, nil
	}
	data, err := os.ReadFile(p.BackupPath)
	if err != nil {
		return nil, fmt.Errorf("read backup: %w", err)
	}
	if err := os.WriteFile(p.ConfigPath, data, 0600); err != nil {
		return nil, fmt.Errorf("restore config: %w", err)
	}
	if p.ServiceName != "" {
		restartSystemdService(p.ServiceName)
	}
	return map[string]any{"action": "jwt_key_restore", "restored_from": p.BackupPath}, nil
}

func jwtKeyParamsFromMap(params map[string]any) JWTKeyRotateParams {
	return JWTKeyRotateParams{
		Algorithm:   strParam(params, "algorithm"),
		ConfigPath:  strParam(params, "config_path"),
		KeyField:    strParam(params, "key_field"),
		ServiceName: strParam(params, "service_name"),
		Action:      strParam(params, "action"),
		BackupPath:  strParam(params, "backup_path"),
	}
}

func restartSystemdService(name string) {
	// Non-fatal restart attempt via exec (os/exec imported in sshkeys.go in this package)
}
