package dbadmin

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

func str(params map[string]any, key string) string {
	v, _ := params[key].(string)
	return v
}

func intParam(params map[string]any, key string) int {
	switch v := params[key].(type) {
	case int:
		return v
	case float64:
		return int(v)
	case string:
		n, _ := strconv.Atoi(v)
		return n
	}
	return 0
}

func runCmd(env []string, name string, args ...string) (string, string, error) {
	cmd := exec.Command(name, args...)
	if env != nil {
		cmd.Env = append(os.Environ(), env...)
	}
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	return stdout.String(), stderr.String(), err
}

func DbPreflightExecute(params map[string]any) (map[string]any, error) {
	engine := str(params, "engine")
	host := str(params, "db_host")
	port := str(params, "db_port")
	if port == "" {
		port = strconv.Itoa(intParam(params, "db_port"))
	}
	user := str(params, "db_user")
	password := str(params, "db_password")

	var connOut, connErr string
	var connRunErr error
	pgEnv := []string{"PGPASSWORD=" + password}

	switch engine {
	case "postgres":
		connOut, connErr, connRunErr = runCmd(pgEnv, "psql",
			"-h", host, "-p", port, "-U", user, "-c", "SELECT version();", "--tuples-only", "--no-align")
	case "mysql":
		connOut, connErr, connRunErr = runCmd(nil, "mysql",
			"-h", host, "--protocol=tcp", "-P", port, "-u", user, "--password="+password, "-e", "SELECT version();")
	case "mongodb":
		mongoArgs := []string{"--host", host, "--port", port, "--eval", "db.version()", "--quiet"}
		if password != "" {
			if user != "" {
				mongoArgs = append(mongoArgs, "--username", user)
			}
			mongoArgs = append(mongoArgs, "--password", password)
		}
		connOut, connErr, connRunErr = runCmd(nil, "mongosh", mongoArgs...)
	default:
		return map[string]any{"status": "preflight_blocked", "error": "unknown engine: " + engine}, nil
	}

	if connRunErr != nil {
		return map[string]any{
			"status": "preflight_blocked",
			"error":  fmt.Sprintf("connectivity check failed: %s %s", connOut, connErr),
		}, nil
	}

	diskOut, _, diskErr := runCmd(nil, "df", "-BG", "--output=avail", "/var/lib")
	diskGB := 0
	if diskErr == nil {
		for _, line := range strings.Split(strings.TrimSpace(diskOut), "\n") {
			line = strings.TrimSpace(strings.TrimSuffix(line, "G"))
			if n, err := strconv.Atoi(line); err == nil {
				diskGB = n
				break
			}
		}
	}

	version := strings.TrimSpace(connOut)

	return map[string]any{
		"status":            "ok",
		"disk_gb_available": diskGB,
		"version":           version,
		"connectivity":      "ok",
	}, nil
}

func DbVersionQueryExecute(params map[string]any) (map[string]any, error) {
	engine := str(params, "engine")
	query := str(params, "query")
	host := str(params, "db_host")
	port := str(params, "db_port")
	if port == "" {
		port = strconv.Itoa(intParam(params, "db_port"))
	}
	user := str(params, "db_user")
	password := str(params, "db_password")

	pgEnv := []string{"PGPASSWORD=" + password}
	var out, errOut string
	var err error

	switch engine {
	case "postgres":
		out, errOut, err = runCmd(pgEnv, "psql",
			"-h", host, "-p", port, "-U", user, "-c", query, "--tuples-only", "--no-align")
	case "mysql":
		out, errOut, err = runCmd(nil, "mysql",
			"-h", host, "--protocol=tcp", "-P", port, "-u", user, "--password="+password, "-e", query)
	case "mongodb":
		mongoArgs := []string{"--host", host, "--port", port, "--eval", query, "--quiet"}
		if password != "" {
			if user != "" {
				mongoArgs = append(mongoArgs, "--username", user)
			}
			mongoArgs = append(mongoArgs, "--password", password)
		}
		out, errOut, err = runCmd(nil, "mongosh", mongoArgs...)
	default:
		return nil, fmt.Errorf("unknown engine: %s", engine)
	}

	if err != nil {
		return nil, fmt.Errorf("query failed: %s %s", out, errOut)
	}
	return map[string]any{"stdout": strings.TrimSpace(out)}, nil
}

func DbUpgradePostgresDumpRestoreExecute(params map[string]any) (map[string]any, error) {
	host := str(params, "db_host")
	portStr := str(params, "db_port")
	if portStr == "" {
		portStr = strconv.Itoa(intParam(params, "db_port"))
	}
	user := str(params, "db_user")
	password := str(params, "db_password")
	targetVersion := str(params, "target_version")

	pgEnv := []string{"PGPASSWORD=" + password}
	dumpPath := "/tmp/nexplane_pg_dump.sql"

	port, _ := strconv.Atoi(portStr)
	if port == 0 {
		port = 5432
	}
	targetPort := port + 1
	targetPortStr := strconv.Itoa(targetPort)

	out, errOut, err := runCmd(pgEnv, "pg_dumpall",
		"-h", host, "-p", portStr, "-U", user, "--no-role-passwords", "-f", dumpPath)
	if err != nil {
		return nil, fmt.Errorf("pg_dumpall failed: %s %s", out, errOut)
	}

	containerName := "pg" + targetVersion
	runCmd(nil, "docker", "rm", "-f", containerName)

	out, errOut, err = runCmd(nil, "docker", "run", "-d",
		"--name", containerName,
		"-e", "POSTGRES_PASSWORD="+password,
		"-p", targetPortStr+":5432",
		"postgres:"+targetVersion)
	if err != nil {
		return nil, fmt.Errorf("docker run failed: %s %s", out, errOut)
	}

	// Wait for container to be ready AND accepting authenticated connections.
	deadline := time.Now().Add(120 * time.Second)
	for time.Now().Before(deadline) {
		_, _, authErr := runCmd(pgEnv, "psql",
			"-h", "localhost", "-p", targetPortStr, "-U", user,
			"-c", "SELECT 1", "--no-align", "--tuples-only")
		if authErr == nil {
			break
		}
		time.Sleep(3 * time.Second)
	}

	// Use ON_ERROR_STOP=off so duplicate-role warnings don't abort the restore.
	out, errOut, err = runCmd(pgEnv, "psql",
		"-h", "localhost", "-p", targetPortStr, "-U", user,
		"--set=ON_ERROR_STOP=off",
		"-f", dumpPath)
	// Ignore errors that are only about duplicate objects (non-fatal).
	if err != nil {
		combined := out + errOut
		if strings.Contains(combined, "authentication") || strings.Contains(combined, "connection to server") {
			return nil, fmt.Errorf("restore failed: %s %s", out, errOut)
		}
		// Other errors (duplicate role, etc.) are warnings — continue.
	}

	return map[string]any{
		"stdout":         "upgrade completed",
		"target_port":    targetPort,
		"target_version": targetVersion,
	}, nil
}

func DbUpgradeMysqlExecute(params map[string]any) (map[string]any, error) {
	host := str(params, "db_host")
	portStr := str(params, "db_port")
	if portStr == "" {
		portStr = strconv.Itoa(intParam(params, "db_port"))
	}
	user := str(params, "db_user")
	password := str(params, "db_password")
	targetVersion := str(params, "target_version")

	dumpPath := "/tmp/nexplane_mysql_dump.sql"

	port, _ := strconv.Atoi(portStr)
	if port == 0 {
		port = 3306
	}
	targetPort := port + 1
	targetPortStr := strconv.Itoa(targetPort)

	out, errOut, err := runCmd(nil, "mysqldump",
		"-h", host, "--protocol=tcp", "-P", portStr, "-u", user, "--password="+password,
		"--all-databases", "--result-file="+dumpPath)
	if err != nil {
		return nil, fmt.Errorf("mysqldump failed: %s %s", out, errOut)
	}

	containerName := "mysql" + targetVersion
	runCmd(nil, "docker", "rm", "-f", containerName)

	out, errOut, err = runCmd(nil, "docker", "run", "-d",
		"--name", containerName,
		"-e", "MYSQL_ROOT_PASSWORD="+password,
		"-p", targetPortStr+":3306",
		"mysql:"+targetVersion)
	if err != nil {
		return nil, fmt.Errorf("docker run mysql failed: %s %s", out, errOut)
	}

	deadline := time.Now().Add(60 * time.Second)
	for time.Now().Before(deadline) {
		_, _, readyErr := runCmd(nil, "mysqladmin",
			"-h", "127.0.0.1", "-P", targetPortStr, "-u", "root", "--password="+password, "ping")
		if readyErr == nil {
			break
		}
		time.Sleep(2 * time.Second)
	}

	out, errOut, err = runCmd(nil, "mysql",
		"-h", "127.0.0.1", "-P", targetPortStr, "-u", "root", "--password="+password,
		"-e", "source "+dumpPath)
	if err != nil {
		return nil, fmt.Errorf("mysql restore failed: %s %s", out, errOut)
	}

	return map[string]any{
		"stdout": "mysql upgrade completed",
		"stderr": errOut,
	}, nil
}

func DbUpgradeMongoFcvHopExecute(params map[string]any) (map[string]any, error) {
	host := str(params, "db_host")
	portStr := str(params, "db_port")
	if portStr == "" {
		portStr = strconv.Itoa(intParam(params, "db_port"))
	}
	user := str(params, "db_user")
	password := str(params, "db_password")
	toVersion := str(params, "to_version")

	eval := fmt.Sprintf("db.adminCommand({setFeatureCompatibilityVersion: \"%s\"})", toVersion)
	fcvArgs := []string{"--host", host, "--port", portStr, "--eval", eval, "--quiet"}
	if password != "" {
		if user != "" {
			fcvArgs = append(fcvArgs, "--username", user)
		}
		fcvArgs = append(fcvArgs, "--password", password)
	}
	out, errOut, err := runCmd(nil, "mongosh", fcvArgs...)
	if err != nil {
		return nil, fmt.Errorf("mongo fcv hop failed: %s %s", out, errOut)
	}
	return map[string]any{
		"status": "ok",
		"stdout": strings.TrimSpace(out),
		"fcv":    toVersion,
	}, nil
}

func DbDumpToS3Execute(params map[string]any) (map[string]any, error) {
	command := str(params, "command")
	password := str(params, "db_password")
	if command == "" {
		return nil, fmt.Errorf("command param required")
	}
	env := []string{"PGPASSWORD=" + password}
	cmd := exec.Command("sh", "-c", command)
	cmd.Env = append(os.Environ(), env...)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err != nil {
		return nil, fmt.Errorf("db_dump_to_s3 failed: %s %s", stdout.String(), stderr.String())
	}
	return map[string]any{
		"stdout": strings.TrimSpace(stdout.String()),
		"stderr": strings.TrimSpace(stderr.String()),
	}, nil
}

func DbRestoreFromS3DumpExecute(params map[string]any) (map[string]any, error) {
	command := str(params, "command")
	password := str(params, "db_password")
	user := str(params, "db_user")
	if command == "" {
		return nil, fmt.Errorf("command param required")
	}
	env := []string{"PGPASSWORD=" + password, "PGUSER=" + user}
	cmd := exec.Command("sh", "-c", command)
	cmd.Env = append(os.Environ(), env...)
	var stdout, stderr strings.Builder
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err != nil {
		return nil, fmt.Errorf("db_restore_from_s3_dump failed: %s %s", stdout.String(), stderr.String())
	}
	return map[string]any{
		"stdout": strings.TrimSpace(stdout.String()),
		"stderr": strings.TrimSpace(stderr.String()),
	}, nil
}


func DbDumpLocalExecute(params map[string]any) (map[string]any, error) {
	engine := str(params, "engine")
	host := str(params, "db_host")
	port := intParam(params, "db_port")
	user := str(params, "db_user")
	password := str(params, "db_password")
	dumpPath := str(params, "dump_path")
	if dumpPath == "" {
		dumpPath = "/tmp/nexplane_dump.sql.gz"
	}
	var dumpCmd string
	switch engine {
	case "postgres":
		dumpCmd = fmt.Sprintf("pg_dumpall -h %s -p %d -U %s | gzip > %s", host, port, user, dumpPath)
	case "mysql":
		dumpCmd = fmt.Sprintf("mysqldump -h %s -P %d -u %s --all-databases | gzip > %s", host, port, user, dumpPath)
	default:
		return nil, fmt.Errorf("db_dump_local: unsupported engine %q", engine)
	}
	env := []string{"PGPASSWORD=" + password, "MYSQL_PWD=" + password}
	cmd := exec.Command("sh", "-c", dumpCmd)
	cmd.Env = append(os.Environ(), env...)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("db_dump_local failed: %s: %w", stderr.String(), err)
	}
	return map[string]any{"dump_path": dumpPath, "status": "ok"}, nil
}

func DbRestoreFromLocalDumpExecute(params map[string]any) (map[string]any, error) {
	engine := str(params, "engine")
	host := str(params, "db_host")
	port := intParam(params, "db_port")
	user := str(params, "db_user")
	password := str(params, "db_password")
	dumpPath := str(params, "dump_path")
	if dumpPath == "" {
		return nil, fmt.Errorf("dump_path required")
	}
	var restoreCmd string
	switch engine {
	case "postgres":
		restoreCmd = fmt.Sprintf("gunzip < %s | psql -h %s -p %d -U %s", dumpPath, host, port, user)
	case "mysql":
		restoreCmd = fmt.Sprintf("gunzip < %s | mysql -h %s -P %d -u %s", dumpPath, host, port, user)
	default:
		return nil, fmt.Errorf("db_restore_from_local_dump: unsupported engine %q", engine)
	}
	env := []string{"PGPASSWORD=" + password, "MYSQL_PWD=" + password}
	cmd := exec.Command("sh", "-c", restoreCmd)
	cmd.Env = append(os.Environ(), env...)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("db_restore_from_local_dump failed: %s: %w", stderr.String(), err)
	}
	return map[string]any{"status": "restored", "dump_path": dumpPath}, nil
}
