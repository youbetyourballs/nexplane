//go:build !linux && !darwin

package appdiscovery

func discoverApplicationsOS(params map[string]any) ([]Application, error) {
	return []Application{}, nil
}
