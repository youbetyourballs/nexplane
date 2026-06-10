package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	baseURL    string
	secret     string
	httpClient *http.Client
}

func New(baseURL, secret string) *Client {
	return &Client{
		baseURL: baseURL,
		secret:  secret,
		httpClient: &http.Client{
			Timeout: 40 * time.Second,
		},
	}
}

type RegisterRequest struct {
	MachineID    string   `json:"machine_id"`
	Hostname     string   `json:"hostname"`
	OsType       string   `json:"os_type"`
	IPAddresses  []string `json:"ip_addresses"`
	OsVersion    string   `json:"os_version"`
	AgentVersion string   `json:"agent_version"`
}

type RegisterResponse struct {
	AgentID string `json:"agent_id"`
	AssetID string `json:"asset_id"`
}

type JobResponse struct {
	JobID         string         `json:"job_id"`
	Command       string         `json:"command"`
	Parameters    map[string]any `json:"parameters"`
	HMACSignature string         `json:"hmac_signature"`
}

type JobResult struct {
	AgentID string         `json:"agent_id"`
	Status  string         `json:"status"`
	Result  map[string]any `json:"result,omitempty"`
	Error   string         `json:"error,omitempty"`
}

func (c *Client) Register(ctx context.Context, req RegisterRequest) (*RegisterResponse, error) {
	var resp RegisterResponse
	if err := c.post(ctx, "/agent/register", req, &resp); err != nil {
		return nil, err
	}
	return &resp, nil
}

// PollNextJob long-polls for the next pending job. Returns nil, nil when no job available (204).
func (c *Client) PollNextJob(ctx context.Context, agentID string) (*JobResponse, error) {
	url := fmt.Sprintf("%s/agent/jobs/next?agent_id=%s", c.baseURL, agentID)
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	httpReq.Header.Set("Authorization", "Bearer "+c.secret)

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode == http.StatusNoContent {
		return nil, nil
	}
	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("poll returned %d: %s", resp.StatusCode, body)
	}

	var job JobResponse
	if err := json.NewDecoder(resp.Body).Decode(&job); err != nil {
		return nil, fmt.Errorf("decoding job response: %w", err)
	}
	return &job, nil
}

func (c *Client) PostResult(ctx context.Context, jobID string, result JobResult) error {
	return c.post(ctx, fmt.Sprintf("/agent/jobs/%s/result", jobID), result, nil)
}

func (c *Client) post(ctx context.Context, path string, body, out any) error {
	data, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshaling request: %w", err)
	}

	url := c.baseURL + path
	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(data))
	if err != nil {
		return err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+c.secret)

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		bodyBytes, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("request to %s returned %d: %s", path, resp.StatusCode, bodyBytes)
	}

	if out != nil {
		return json.NewDecoder(resp.Body).Decode(out)
	}
	return nil
}
