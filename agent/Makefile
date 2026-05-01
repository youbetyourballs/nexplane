.PHONY: build build-linux build-windows test clean

GOENV_LINUX_AMD64=GOOS=linux GOARCH=amd64
GOENV_LINUX_ARM64=GOOS=linux GOARCH=arm64
GOENV_WINDOWS=GOOS=windows GOARCH=amd64

build: build-linux build-windows

build-linux:
	$(GOENV_LINUX_AMD64) go build -o dist/nexplane-agent-linux-amd64 ./
	$(GOENV_LINUX_ARM64) go build -o dist/nexplane-agent-linux-arm64 ./

build-windows:
	$(GOENV_WINDOWS) go build -o dist/nexplane-agent-windows-amd64.exe ./

test:
	go test ./... -v

clean:
	rm -rf dist/
