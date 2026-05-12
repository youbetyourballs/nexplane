package listpkgs

import (
	"testing"
)

// TestParseTSVDpkg verifies that parseTSV correctly parses dpkg-query output.
func TestParseTSVDpkg(t *testing.T) {
	input := "adduser\t3.118ubuntu5\nbase-files\t12ubuntu4.4\nbash\t5.1-6ubuntu1.1\n"
	pkgs := parseTSV(input, "dpkg")

	if len(pkgs) != 3 {
		t.Fatalf("expected 3 packages, got %d", len(pkgs))
	}

	cases := []struct{ name, version, manager string }{
		{"adduser", "3.118ubuntu5", "dpkg"},
		{"base-files", "12ubuntu4.4", "dpkg"},
		{"bash", "5.1-6ubuntu1.1", "dpkg"},
	}
	for i, c := range cases {
		if pkgs[i]["name"] != c.name {
			t.Errorf("[%d] name: want %q got %q", i, c.name, pkgs[i]["name"])
		}
		if pkgs[i]["version"] != c.version {
			t.Errorf("[%d] version: want %q got %q", i, c.version, pkgs[i]["version"])
		}
		if pkgs[i]["manager"] != c.manager {
			t.Errorf("[%d] manager: want %q got %q", i, c.manager, pkgs[i]["manager"])
		}
	}
}

// TestParseTSVRpm verifies RPM queryformat output parsing.
func TestParseTSVRpm(t *testing.T) {
	input := "glibc\t2.34-60.amzn2023.0.1\ncurl\t7.88.1-1.amzn2023\n"
	pkgs := parseTSV(input, "rpm")
	if len(pkgs) != 2 {
		t.Fatalf("expected 2 packages, got %d", len(pkgs))
	}
	if pkgs[0]["name"] != "glibc" || pkgs[0]["manager"] != "rpm" {
		t.Errorf("unexpected first package: %v", pkgs[0])
	}
}

// TestParseTSVEmpty verifies that empty output produces no packages.
func TestParseTSVEmpty(t *testing.T) {
	pkgs := parseTSV("", "dpkg")
	if len(pkgs) != 0 {
		t.Errorf("expected 0 packages from empty input, got %d", len(pkgs))
	}
}

// TestCap1000 verifies the 1000-package cap.
func TestCap1000(t *testing.T) {
	var big []map[string]any
	for i := 0; i < 1500; i++ {
		big = append(big, map[string]any{"name": "pkg", "version": "1.0", "manager": "dpkg"})
	}
	capped := cap1000(big)
	if len(capped) != 1000 {
		t.Errorf("expected 1000 packages after cap, got %d", len(capped))
	}
}

// TestParseApk verifies Alpine apk list output parsing.
func TestParseApk(t *testing.T) {
	input := "musl-1.2.3-r0 x86_64 {musl} (MIT) [installed]\nbusybox-1.36.1-r0 x86_64 {busybox} (GPL-2.0) [installed]\n"
	pkgs := parseApk(input)
	if len(pkgs) != 2 {
		t.Fatalf("expected 2 packages, got %d", len(pkgs))
	}
	if pkgs[0]["name"] != "musl" || pkgs[0]["version"] != "1.2.3-r0" {
		t.Errorf("unexpected first apk package: %v", pkgs[0])
	}
}

// TestParseSnap verifies snap list output parsing.
func TestParseSnap(t *testing.T) {
	input := "Name       Version   Rev    Tracking       Publisher  Notes\ncore20     20230801  1974   latest/stable  canonical  base\nhello      2.10      20     latest/stable  canonical  -\n"
	pkgs := parseSnap(input)
	if len(pkgs) != 2 {
		t.Fatalf("expected 2 packages, got %d", len(pkgs))
	}
	if pkgs[0]["name"] != "core20" || pkgs[0]["version"] != "20230801" || pkgs[0]["manager"] != "snap" {
		t.Errorf("unexpected first snap package: %v", pkgs[0])
	}
}
