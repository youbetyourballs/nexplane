package uploadimage

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"fmt"
	"io"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
	"github.com/aws/aws-sdk-go-v2/feature/s3/manager"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

func Execute(params map[string]any) (map[string]any, error) {
	imagePath, _ := params["image_path"].(string)
	destURI, _ := params["destination_uri"].(string)

	if imagePath == "" {
		return nil, fmt.Errorf("image_path is required")
	}
	if destURI == "" {
		return nil, fmt.Errorf("destination_uri is required")
	}

	u, err := url.Parse(destURI)
	if err != nil {
		return nil, fmt.Errorf("invalid destination_uri: %w", err)
	}
	if u.Scheme != "s3" {
		return nil, fmt.Errorf("unsupported destination scheme %q — only 's3://' is supported", u.Scheme)
	}

	bucket := u.Host
	key := strings.TrimPrefix(u.Path, "/")

	awsCfg, err := buildAWSConfig(params)
	if err != nil {
		return nil, fmt.Errorf("loading AWS config: %w", err)
	}

	f, err := os.Open(imagePath)
	if err != nil {
		return nil, fmt.Errorf("opening image file: %w", err)
	}
	defer f.Close()

	info, err := f.Stat()
	if err != nil {
		return nil, fmt.Errorf("stat image file: %w", err)
	}

	h := md5.New()
	if _, err := io.Copy(h, f); err != nil {
		return nil, fmt.Errorf("computing checksum: %w", err)
	}
	localMD5 := hex.EncodeToString(h.Sum(nil))
	f.Seek(0, io.SeekStart)

	s3Client := s3.NewFromConfig(awsCfg)
	uploader := manager.NewUploader(s3Client, func(u *manager.Uploader) {
		u.PartSize = 64 * 1024 * 1024
	})

	result, err := uploader.Upload(context.Background(), &s3.PutObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
		Body:   f,
	})
	if err != nil {
		return nil, fmt.Errorf("uploading to S3: %w", err)
	}

	checksumVerified := false
	head, err := s3Client.HeadObject(context.Background(), &s3.HeadObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err == nil && head.ContentLength != nil && *head.ContentLength == info.Size() {
		checksumVerified = true
	}

	etag := ""
	if result.ETag != nil {
		etag = strings.Trim(*result.ETag, `"`)
	}

	return map[string]any{
		"action":            "upload_image",
		"image_path":        imagePath,
		"destination_uri":   destURI,
		"size_bytes":        info.Size(),
		"local_md5":         localMD5,
		"etag":              etag,
		"checksum_verified": checksumVerified,
		"uploaded_at":       time.Now().UTC().Format(time.RFC3339),
	}, nil
}

func Rollback(params map[string]any) (map[string]any, error) {
	destURI, _ := params["destination_uri"].(string)
	if destURI == "" {
		return nil, fmt.Errorf("destination_uri is required for rollback")
	}

	u, err := url.Parse(destURI)
	if err != nil {
		return nil, fmt.Errorf("invalid destination_uri: %w", err)
	}
	bucket := u.Host
	key := strings.TrimPrefix(u.Path, "/")

	awsCfg, err := buildAWSConfig(params)
	if err != nil {
		return nil, fmt.Errorf("loading AWS config: %w", err)
	}

	s3Client := s3.NewFromConfig(awsCfg)
	_, err = s3Client.DeleteObject(context.Background(), &s3.DeleteObjectInput{
		Bucket: aws.String(bucket),
		Key:    aws.String(key),
	})
	if err != nil {
		return nil, fmt.Errorf("deleting S3 object: %w", err)
	}

	return map[string]any{
		"rolled_back":     true,
		"destination_uri": destURI,
		"deleted":         true,
	}, nil
}

func buildAWSConfig(params map[string]any) (aws.Config, error) {
	opts := []func(*config.LoadOptions) error{}
	if region, ok := params["region"].(string); ok && region != "" {
		opts = append(opts, config.WithRegion(region))
	}
	if accessKey, ok := params["access_key_id"].(string); ok && accessKey != "" {
		secretKey, _ := params["secret_access_key"].(string)
		opts = append(opts, config.WithCredentialsProvider(
			credentials.NewStaticCredentialsProvider(accessKey, secretKey, ""),
		))
	}
	return config.LoadDefaultConfig(context.Background(), opts...)
}
