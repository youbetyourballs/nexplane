package main

import (
	"fmt"
	"os"

	"nexplane-agent/config"
)

func main() {
	cfg, err := config.Load(os.Args[1:])
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("Nexplane Agent starting (mode=%s, control-plane=%s)\n", cfg.Mode, cfg.ControlPlane)
}
