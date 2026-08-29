.PHONY: build build-linux build-windows build-darwin test clean

VERSION ?= $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "0.4.6")
LDFLAGS=-ldflags "-X main.Version=$(VERSION)"

GOENV_LINUX_AMD64=GOOS=linux GOARCH=amd64
GOENV_LINUX_ARM64=GOOS=linux GOARCH=arm64
GOENV_WINDOWS=GOOS=windows GOARCH=amd64
GOENV_DARWIN_ARM64=CGO_ENABLED=0 GOOS=darwin GOARCH=arm64

build: build-linux build-windows build-darwin

build-linux:
	$(GOENV_LINUX_AMD64) go build $(LDFLAGS) -o dist/nexplane-agent-linux-amd64 ./
	$(GOENV_LINUX_ARM64) go build $(LDFLAGS) -o dist/nexplane-agent-linux-arm64 ./

build-windows:
	$(GOENV_WINDOWS) go build $(LDFLAGS) -o dist/nexplane-agent-windows-amd64.exe ./

build-darwin:
	$(GOENV_DARWIN_ARM64) go build $(LDFLAGS) -o dist/nexplane-agent-darwin-arm64 ./

test:
	go test ./... -v

clean:
	rm -rf dist/
