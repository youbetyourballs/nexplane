// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2024-2026 Nexplane, Inc.

package containerizebuild

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

// BuildAndPush builds a Docker image from the provided app profile and pushes it.
// If dryRun is true, no docker commands are executed and ("", nil) is returned.
// Returns the sha256 digest of the pushed image on success.
//
// Build context: a temp directory is created and the binary + config files are
// copied into it so that relative-path COPY instructions in the Dockerfile work.
// ECR auth: if the registry looks like an ECR endpoint, we authenticate via
// `aws ecr get-login-password` using instance metadata credentials before pushing.
func BuildAndPush(imageName, dockerfile string, app AppProfile, dryRun bool) (string, error) {
	if dryRun {
		return "", nil
	}

	// Create a temp build context directory
	buildCtx, err := os.MkdirTemp("", "nexplane-build-*")
	if err != nil {
		return "", fmt.Errorf("failed to create build context dir: %w", err)
	}
	defer os.RemoveAll(buildCtx)

	// Stage the binary into the build context (preserving relative path under buildCtx)
	if app.Binary != "" {
		if err := stageFile(buildCtx, app.Binary); err != nil {
			return "", fmt.Errorf("failed to stage binary %s: %w", app.Binary, err)
		}
	}

	// Stage config files
	for _, cf := range app.ConfigFiles {
		if cf == "" {
			continue
		}
		if err := stageFile(buildCtx, cf); err != nil {
			// Non-fatal: warn but continue — config files may not exist on all hosts
			fmt.Fprintf(os.Stderr, "warning: could not stage config file %s: %v\n", cf, err)
		}
	}

	// Write Dockerfile into the build context
	dockerfilePath := filepath.Join(buildCtx, "Dockerfile")
	if err := os.WriteFile(dockerfilePath, []byte(dockerfile), 0o644); err != nil {
		return "", fmt.Errorf("failed to write Dockerfile: %w", err)
	}

	// Authenticate to ECR if the registry looks like an ECR endpoint
	if err := ecrLoginIfNeeded(imageName); err != nil {
		return "", fmt.Errorf("ECR login failed: %w", err)
	}

	// docker build --tag imageName <buildCtx>
	buildCmd := exec.Command("docker", "build", "--tag", imageName, buildCtx)
	var buildOut, buildErr bytes.Buffer
	buildCmd.Stdout = &buildOut
	buildCmd.Stderr = &buildErr

	if err := buildCmd.Run(); err != nil {
		return "", fmt.Errorf("docker build failed: %w\nstderr: %s", err, buildErr.String())
	}

	// docker push --quiet imageName
	pushCmd := exec.Command("docker", "push", "--quiet", imageName)
	var pushOut, pushErr bytes.Buffer
	pushCmd.Stdout = &pushOut
	pushCmd.Stderr = &pushErr

	if err := pushCmd.Run(); err != nil {
		return "", fmt.Errorf("docker push failed: %w\nstderr: %s", err, pushErr.String())
	}

	// Extract sha256 digest from push output
	digest := extractDigest(pushOut.String())
	return digest, nil
}

// stageFile copies src (absolute path on host) into buildCtx preserving the
// relative directory structure so that COPY instructions work correctly.
func stageFile(buildCtx, src string) error {
	// Ensure src is absolute
	if !filepath.IsAbs(src) {
		return fmt.Errorf("source path must be absolute: %s", src)
	}
	// Destination: strip leading slash so the file sits under buildCtx
	rel := strings.TrimPrefix(src, "/")
	dst := filepath.Join(buildCtx, rel)
	if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
		return fmt.Errorf("mkdir %s: %w", filepath.Dir(dst), err)
	}
	// cp -p preserves permissions; simpler than reimplementing in Go
	cpCmd := exec.Command("cp", "-p", src, dst)
	if out, err := cpCmd.CombinedOutput(); err != nil {
		return fmt.Errorf("cp %s → %s: %w\n%s", src, dst, err, out)
	}
	return nil
}

// ecrLoginIfNeeded runs `aws ecr get-login-password | docker login` when imageName
// targets an ECR registry. Uses instance metadata credentials (IAM role) — no
// explicit AWS credentials are needed.
func ecrLoginIfNeeded(imageName string) error {
	// ECR endpoints look like: 123456789.dkr.ecr.us-east-1.amazonaws.com/...
	if !strings.Contains(imageName, ".dkr.ecr.") {
		return nil
	}

	// Extract region from the hostname
	host := strings.SplitN(imageName, "/", 2)[0]
	// host = 614130399980.dkr.ecr.us-east-1.amazonaws.com
	parts := strings.Split(host, ".")
	// parts: [614130399980, dkr, ecr, us-east-1, amazonaws, com]
	region := ""
	for i, p := range parts {
		if p == "ecr" && i+1 < len(parts) {
			region = parts[i+1]
			break
		}
	}
	if region == "" {
		return fmt.Errorf("could not extract region from ECR registry %s", host)
	}

	// aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <host>
	getPwd := exec.Command("aws", "ecr", "get-login-password", "--region", region)
	login := exec.Command("docker", "login", "--username", "AWS", "--password-stdin", host)

	var getPwdErr, loginOut, loginErr bytes.Buffer
	getPwd.Stderr = &getPwdErr

	pipe, err := getPwd.StdoutPipe()
	if err != nil {
		return fmt.Errorf("pipe setup failed: %w", err)
	}
	login.Stdin = pipe
	login.Stdout = &loginOut
	login.Stderr = &loginErr

	if err := getPwd.Start(); err != nil {
		return fmt.Errorf("aws ecr get-login-password start: %w", err)
	}
	if err := login.Start(); err != nil {
		return fmt.Errorf("docker login start: %w", err)
	}
	if err := getPwd.Wait(); err != nil {
		return fmt.Errorf("aws ecr get-login-password failed: %w\nstderr: %s", err, getPwdErr.String())
	}
	if err := login.Wait(); err != nil {
		return fmt.Errorf("docker login failed: %w\nstderr: %s", err, loginErr.String())
	}
	return nil
}

// DeleteImage removes a Docker image from ECR (or any registry) by name/digest.
// Parameters: image_name (required), image_digest (optional).
// Returns {"deleted": true/false, "image_name": "..."}.
func DeleteImage(imageName, imageDigest string) (map[string]any, error) {
	if imageName == "" {
		return map[string]any{"deleted": false, "reason": "no_image_name"}, nil
	}

	// Authenticate to ECR if needed before attempting delete
	if err := ecrLoginIfNeeded(imageName); err != nil {
		return nil, fmt.Errorf("ECR login failed: %w", err)
	}

	// If we have an ECR registry + digest, delete from ECR directly via AWS CLI
	// (docker rmi only removes locally; ECR delete-image removes from registry)
	if strings.Contains(imageName, ".dkr.ecr.") && imageDigest != "" {
		host := strings.SplitN(imageName, "/", 2)[0]
		parts := strings.Split(host, ".")
		region := ""
		for i, p := range parts {
			if p == "ecr" && i+1 < len(parts) {
				region = parts[i+1]
				break
			}
		}
		// repo name is everything after host/
		imageRef := strings.SplitN(imageName, "/", 2)
		repoWithTag := ""
		if len(imageRef) > 1 {
			repoWithTag = imageRef[1]
		}
		// strip tag to get repo name only
		repoName := strings.SplitN(repoWithTag, ":", 2)[0]
		if region != "" && repoName != "" {
			digest := imageDigest
			if !strings.HasPrefix(digest, "sha256:") {
				digest = "sha256:" + digest
			}
			delCmd := exec.Command(
				"aws", "ecr", "batch-delete-image",
				"--region", region,
				"--repository-name", repoName,
				"--image-ids", fmt.Sprintf("imageDigest=%s", digest),
			)
			var delOut, delErr bytes.Buffer
			delCmd.Stdout = &delOut
			delCmd.Stderr = &delErr
			if err := delCmd.Run(); err != nil {
				return nil, fmt.Errorf("ecr batch-delete-image failed: %w\nstderr: %s", err, delErr.String())
			}
			return map[string]any{"deleted": true, "image_name": imageName, "image_digest": imageDigest}, nil
		}
	}

	// Fallback: docker rmi (removes locally; best-effort for non-ECR)
	rmiCmd := exec.Command("docker", "rmi", "--force", imageName)
	var rmiOut, rmiErr bytes.Buffer
	rmiCmd.Stdout = &rmiOut
	rmiCmd.Stderr = &rmiErr
	if err := rmiCmd.Run(); err != nil {
		return nil, fmt.Errorf("docker rmi failed: %w\nstderr: %s", err, rmiErr.String())
	}
	return map[string]any{"deleted": true, "image_name": imageName}, nil
}

func extractDigest(output string) string {
	for _, line := range strings.Split(output, "\n") {
		if idx := strings.Index(line, "sha256:"); idx >= 0 {
			parts := strings.Fields(line[idx:])
			if len(parts) > 0 {
				return parts[0]
			}
		}
	}
	return ""
}
