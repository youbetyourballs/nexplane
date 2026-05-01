package poller

import (
	"context"
	"fmt"
	"log"
	"time"

	"nexplane-agent/agenthmac"
	"nexplane-agent/client"
	"nexplane-agent/executor"
)

// RunEphemeral polls once for a pending job, executes it, and returns.
func RunEphemeral(ctx context.Context, c *client.Client, agentID, secret string) error {
	job, err := c.PollNextJob(ctx, agentID)
	if err != nil {
		return fmt.Errorf("polling for job: %w", err)
	}
	if job == nil {
		log.Println("No pending jobs.")
		return nil
	}
	return processJob(ctx, c, job, secret)
}

// RunService loops indefinitely, polling for jobs at the given interval.
func RunService(ctx context.Context, c *client.Client, agentID, secret string, pollInterval time.Duration) {
	log.Printf("Starting service mode (poll interval: %s)", pollInterval)
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		job, err := c.PollNextJob(ctx, agentID)
		if err != nil {
			log.Printf("Poll error: %v — retrying in %s", err, pollInterval)
			sleep(ctx, pollInterval)
			continue
		}
		if job == nil {
			sleep(ctx, pollInterval)
			continue
		}

		if err := processJob(ctx, c, job, secret); err != nil {
			log.Printf("Job %s error: %v", job.JobID, err)
		}
	}
}

func processJob(ctx context.Context, c *client.Client, job *client.JobResponse, secret string) error {
	log.Printf("Received job %s: command=%s", job.JobID, job.Command)

	if !agenthmac.Verify(secret, job.JobID, job.Command, job.Parameters, job.HMACSignature) {
		errMsg := "signature verification failed — rejecting job"
		log.Printf("Job %s: %s", job.JobID, errMsg)
		return c.PostResult(ctx, job.JobID, client.JobResult{Status: "failed", Error: errMsg})
	}

	rollback, _ := job.Parameters["rollback"].(bool)
	previousResult, _ := job.Parameters["previous_result"].(map[string]any)

	result := executor.Dispatch(job.Command, job.Parameters, rollback, previousResult)

	jobResult := client.JobResult{
		Status: result.Status,
		Result: result.Data,
		Error:  result.Error,
	}
	if err := c.PostResult(ctx, job.JobID, jobResult); err != nil {
		return fmt.Errorf("posting result: %w", err)
	}
	log.Printf("Job %s completed with status=%s", job.JobID, result.Status)
	return nil
}

func sleep(ctx context.Context, d time.Duration) {
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}
