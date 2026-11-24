package core

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"regexp"
)

var presentationToken = regexp.MustCompile(`^[a-zA-Z0-9_-]{1,160}$`)

// ReadExportableSlides fetches Feishu SML after checking that the authenticated
// application may both view and export this presentation. A read scope alone
// does not authorize copying a presentation into a separate knowledge base.
func (c *Client) ReadExportableSlides(ctx context.Context, token string) (string, error) {
	if !presentationToken.MatchString(token) {
		return "", errors.New("invalid presentation identifier")
	}
	for _, action := range []string{"view", "export"} {
		var response struct {
			ApiResponse
			Data struct {
				Allowed bool `json:"auth_result"`
			} `json:"data"`
		}
		path := "/open-apis/drive/v1/permissions/" + token + "/members/auth?type=slides&action=" + action
		if err := c.doRequest(ctx, http.MethodGet, path, nil, &response, true); err != nil {
			return "", err
		}
		if response.Code != 0 || !response.Data.Allowed {
			return "", fmt.Errorf("slides %s permission not granted: code=%d", action, response.Code)
		}
	}
	var response struct {
		ApiResponse
		Data struct {
			XML struct {
				ID      string `json:"presentation_id"`
				Content string `json:"content"`
			} `json:"xml_presentation"`
		} `json:"data"`
	}
	path := "/open-apis/slides_ai/v1/xml_presentations/" + token
	if err := c.doRequest(ctx, http.MethodGet, path, nil, &response, true); err != nil {
		return "", err
	}
	if response.Code != 0 {
		return "", fmt.Errorf("read slides failed: code=%d", response.Code)
	}
	if response.Data.XML.ID != token || response.Data.XML.Content == "" {
		return "", errors.New("missing or mismatched presentation")
	}
	return response.Data.XML.Content, nil
}
