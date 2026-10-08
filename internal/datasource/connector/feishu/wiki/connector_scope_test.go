package wiki

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/Tencent/WeKnora/internal/datasource/connector/feishu/core"
	"github.com/Tencent/WeKnora/internal/types"
)

func strictOrigin(token string) core.WikiNode {
	return core.WikiNode{SpaceID: "space1", NodeType: "origin", NodeToken: token, ObjToken: "obj-" + token, ObjType: "docx", Title: token, ObjEditTime: "100"}
}

func strictWikiServer(t *testing.T, list func(*http.Request) core.WikiNodeListResponse, roots map[string]core.WikiNode) (*types.DataSourceConfig, *atomic.Int32, *atomic.Int32) {
	t.Helper()
	t.Setenv("FEISHU_DOCX_PARSE_MODE", "export")
	bodyCalls := &atomic.Int32{}
	nodeCalls := &atomic.Int32{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/open-apis/auth/v3/tenant_access_token/internal":
			writeJSON(w, core.TokenResponse{TenantAccessToken: "test-token", Expire: 7200})
		case r.URL.Path == "/open-apis/wiki/v2/spaces/space1/nodes":
			nodeCalls.Add(1)
			writeJSON(w, list(r))
		case r.URL.Path == "/open-apis/wiki/v2/spaces/get_node":
			nodeCalls.Add(1)
			var response core.WikiNodeInfoResponse
			response.Data.Node = roots[r.URL.Query().Get("token")]
			writeJSON(w, response)
		case r.URL.Path == "/open-apis/drive/v1/export_tasks":
			bodyCalls.Add(1)
			writeJSON(w, core.ExportTaskCreateResponse{Data: core.ExportTaskCreateData{Ticket: "ticket"}})
		case r.URL.Path == "/open-apis/drive/v1/export_tasks/ticket":
			bodyCalls.Add(1)
			writeJSON(w, core.ExportTaskStatusResponse{Data: core.ExportTaskStatusData{Result: core.ExportTaskResult{FileToken: "export-file", FileName: "test.docx"}}})
		case r.URL.Path == "/open-apis/drive/v1/export_tasks/file/export-file/download":
			bodyCalls.Add(1)
			_, _ = w.Write([]byte("exported-document"))
		default:
			bodyCalls.Add(1)
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(server.Close)
	config := makeConfig(&core.Config{AppID: "test-app", AppSecret: "test-secret", BaseURL: server.URL}, []string{"space1"})
	config.Settings = map[string]interface{}{"allowed_space_id": "space1"}
	return config, bodyCalls, nodeCalls
}

func strictNodePage(nodes ...core.WikiNode) core.WikiNodeListResponse {
	return core.WikiNodeListResponse{Data: core.WikiNodeListData{Items: nodes}}
}

func strictWikiConnector(config *types.DataSourceConfig) *Connector {
	region := core.RegionFeishu
	region.OpenBaseURL = config.Credentials["base_url"].(string)
	return NewConnector(region)
}

func TestStrictWikiScopeRejectsUnsafeNodesBeforeBody(t *testing.T) {
	tests := []struct {
		name   string
		change func(*core.WikiNode)
	}{
		{"foreign-space", func(n *core.WikiNode) { n.SpaceID = "space2" }},
		{"missing-space", func(n *core.WikiNode) { n.SpaceID = "" }},
		{"same-space-shortcut", func(n *core.WikiNode) { n.NodeType = "shortcut"; n.OriginSpaceID = "space1" }},
		{"foreign-shortcut", func(n *core.WikiNode) { n.NodeType = "shortcut"; n.OriginSpaceID = "space2" }},
		{"missing-node-type", func(n *core.WikiNode) { n.NodeType = "" }},
		{"unknown-node-type", func(n *core.WikiNode) { n.NodeType = "unknown" }},
		{"foreign-origin", func(n *core.WikiNode) { n.OriginSpaceID = "space2" }},
		{"missing-node-token", func(n *core.WikiNode) { n.NodeToken = "" }},
		{"missing-object-token", func(n *core.WikiNode) { n.ObjToken = "" }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			unsafe := strictOrigin("unsafe")
			unsafe.HasChild = true
			test.change(&unsafe)
			config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
				return strictNodePage(strictOrigin("safe"), unsafe)
			}, nil)
			h := &recordingHandler{}
			_, err := strictWikiConnector(config).FetchStream(context.Background(), config, nil, h)
			if err == nil || bodyCalls.Load() != 0 || nodeCalls.Load() != 1 || len(h.emitted) != 0 {
				t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d emitted=%d", err, bodyCalls.Load(), nodeCalls.Load(), len(h.emitted))
			}
		})
	}
}

func TestStrictWikiScopePreflightsEntireTree(t *testing.T) {
	parent := strictOrigin("parent")
	parent.HasChild = true
	foreign := strictOrigin("foreign-child")
	foreign.SpaceID = "space2"
	config, bodyCalls, nodeCalls := strictWikiServer(t, func(r *http.Request) core.WikiNodeListResponse {
		if r.URL.Query().Get("parent_node_token") == "parent" {
			return strictNodePage(foreign)
		}
		return strictNodePage(strictOrigin("safe-first"), parent)
	}, nil)
	h := &recordingHandler{}
	_, err := strictWikiConnector(config).FetchStream(context.Background(), config, nil, h)
	if err == nil || bodyCalls.Load() != 0 || nodeCalls.Load() != 2 || len(h.emitted) != 0 {
		t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d emitted=%d", err, bodyCalls.Load(), nodeCalls.Load(), len(h.emitted))
	}
}

func TestStrictWikiScopePreflightsAllSelectedNodes(t *testing.T) {
	foreign := strictOrigin("second")
	foreign.SpaceID = "space2"
	config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		return strictNodePage()
	}, map[string]core.WikiNode{"first": strictOrigin("first"), "second": foreign})
	config.ResourceIDs = []string{"space1:first", "space1:second"}
	_, err := strictWikiConnector(config).FetchAll(context.Background(), config, config.ResourceIDs)
	if err == nil || bodyCalls.Load() != 0 || nodeCalls.Load() != 2 {
		t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d", err, bodyCalls.Load(), nodeCalls.Load())
	}
}

func TestStrictWikiScopeRejectsInvalidSelections(t *testing.T) {
	for _, resourceID := range []string{"space2", "space2:node", "space1:", "space1:node:extra"} {
		t.Run(resourceID, func(t *testing.T) {
			config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
				return strictNodePage(strictOrigin("safe"))
			}, nil)
			config.ResourceIDs = []string{resourceID}
			_, _, err := strictWikiConnector(config).FetchIncremental(context.Background(), config, nil)
			if err == nil || bodyCalls.Load() != 0 || nodeCalls.Load() != 0 {
				t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d", err, bodyCalls.Load(), nodeCalls.Load())
			}
		})
	}
}

func TestStrictWikiScopeRejectsIncompletePagination(t *testing.T) {
	config, bodyCalls, _ := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		page := strictNodePage(strictOrigin("safe"))
		page.Data.HasMore = true
		return page
	}, nil)
	_, err := strictWikiConnector(config).FetchStream(context.Background(), config, nil, &recordingHandler{})
	if err == nil || !strings.Contains(err.Error(), "pagination") || bodyCalls.Load() != 0 {
		t.Fatalf("err=%v bodyCalls=%d", err, bodyCalls.Load())
	}
}

func TestStrictWikiScopeRejectsBlocksBeforeRequests(t *testing.T) {
	config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		return strictNodePage(strictOrigin("safe"))
	}, nil)
	t.Setenv("FEISHU_DOCX_PARSE_MODE", "blocks")
	_, err := strictWikiConnector(config).FetchStream(context.Background(), config, nil, &recordingHandler{})
	if err == nil || !strings.Contains(err.Error(), "requires FEISHU_DOCX_PARSE_MODE=export") || bodyCalls.Load() != 0 || nodeCalls.Load() != 0 {
		t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d", err, bodyCalls.Load(), nodeCalls.Load())
	}
}

func TestStrictWikiScopeUsesPreflightNodesForFetch(t *testing.T) {
	t.Setenv("FEISHU_DOCX_PARSE_MODE", "export")
	config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		return strictNodePage(strictOrigin("safe"))
	}, nil)
	h := &recordingHandler{}
	_, err := strictWikiConnector(config).FetchStream(context.Background(), config, nil, h)
	if err != nil || bodyCalls.Load() != 3 || nodeCalls.Load() != 1 || len(h.emitted) != 1 || string(h.emitted[0].Content) != "exported-document" {
		t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d emitted=%d", err, bodyCalls.Load(), nodeCalls.Load(), len(h.emitted))
	}
}

func TestStrictWikiScopeIsOptional(t *testing.T) {
	t.Setenv("FEISHU_DOCX_PARSE_MODE", "export")
	node := strictOrigin("legacy")
	node.SpaceID = ""
	node.NodeType = ""
	config, bodyCalls, _ := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		return strictNodePage(node)
	}, nil)
	config.Settings = nil
	h := &recordingHandler{}
	_, err := NewConnector(core.RegionFeishu).FetchStream(context.Background(), config, nil, h)
	if err != nil || bodyCalls.Load() != 3 || len(h.emitted) != 1 {
		t.Fatalf("err=%v bodyCalls=%d emitted=%d", err, bodyCalls.Load(), len(h.emitted))
	}
}

func TestStrictWikiScopeGuardsResourcePickerAndAncestors(t *testing.T) {
	foreign := strictOrigin("foreign")
	foreign.SpaceID = "space2"
	config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		return strictNodePage(foreign)
	}, map[string]core.WikiNode{"foreign": foreign})
	c := strictWikiConnector(config)
	resources, err := c.ListResources(context.Background(), config, "")
	if err != nil || len(resources) != 1 || resources[0].ExternalID != "space1" || nodeCalls.Load() != 0 {
		t.Fatalf("resources=%v err=%v nodeCalls=%d", resources, err, nodeCalls.Load())
	}
	if _, err := c.ListResources(context.Background(), config, "space2"); err == nil {
		t.Fatal("foreign picker selection accepted")
	}
	if _, err := c.ListResources(context.Background(), config, "space1"); err == nil {
		t.Fatal("foreign picker node accepted")
	}
	if _, err := c.ResolveResourceAncestors(context.Background(), config, []string{"space1:foreign"}); err == nil {
		t.Fatal("foreign ancestor accepted")
	}
	if bodyCalls.Load() != 0 {
		t.Fatalf("bodyCalls=%d", bodyCalls.Load())
	}
}

func TestStrictWikiScopeRejectsEmptyAllowedSpace(t *testing.T) {
	config, bodyCalls, nodeCalls := strictWikiServer(t, func(*http.Request) core.WikiNodeListResponse {
		return strictNodePage(strictOrigin("safe"))
	}, nil)
	config.Settings["allowed_space_id"] = ""
	err := strictWikiConnector(config).Validate(context.Background(), config)
	if err == nil || !strings.Contains(err.Error(), "must not be empty") || bodyCalls.Load() != 0 || nodeCalls.Load() != 0 {
		t.Fatalf("err=%v bodyCalls=%d nodeCalls=%d", err, bodyCalls.Load(), nodeCalls.Load())
	}
}

func TestStrictWikiScopeRejectsUntrustedOriginBeforeCredentials(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		http.Error(w, "untrusted origin contacted", http.StatusBadRequest)
	}))
	t.Cleanup(server.Close)
	config := makeConfig(&core.Config{AppID: "test-app", AppSecret: "test-secret", BaseURL: server.URL}, []string{"space1"})
	config.Settings = map[string]interface{}{"allowed_space_id": "space1"}
	c := NewConnector(core.RegionFeishu)
	ctx := context.Background()
	tests := []struct {
		name string
		run  func() error
	}{
		{"validate", func() error { return c.Validate(ctx, config) }},
		{"picker", func() error { _, err := c.ListResources(ctx, config, "space1"); return err }},
		{"ancestors", func() error { _, err := c.ResolveResourceAncestors(ctx, config, []string{"space1:node"}); return err }},
		{"full", func() error { _, err := c.FetchAll(ctx, config, config.ResourceIDs); return err }},
		{"incremental", func() error { _, _, err := c.FetchIncremental(ctx, config, nil); return err }},
		{"stream", func() error { _, err := c.FetchStream(ctx, config, nil, &recordingHandler{}); return err }},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			err := test.run()
			if err == nil || !strings.Contains(err.Error(), "official base_url") || requests.Load() != 0 {
				t.Fatalf("err=%v requests=%d", err, requests.Load())
			}
		})
	}
}

func TestStrictWikiScopeTrustsOnlyRegionOrigin(t *testing.T) {
	t.Setenv("FEISHU_DOCX_PARSE_MODE", "export")
	for _, region := range []core.Region{core.RegionFeishu, core.RegionLark} {
		t.Run(region.ConnectorType, func(t *testing.T) {
			for _, baseURL := range []string{region.OpenBaseURL, region.OpenBaseURL + "/"} {
				config := &types.DataSourceConfig{Settings: map[string]interface{}{"allowed_space_id": "space1"}, Credentials: map[string]interface{}{"base_url": baseURL}}
				if _, err := newStrictWikiScope(config, region); err != nil {
					t.Fatalf("official origin %q rejected: %v", baseURL, err)
				}
			}
			config := &types.DataSourceConfig{Settings: map[string]interface{}{"allowed_space_id": "space1"}}
			if _, err := newStrictWikiScope(config, region); err != nil {
				t.Fatalf("default region origin rejected: %v", err)
			}
			for _, baseURL := range []interface{}{"", nil, 123, region.OpenBaseURL + ".example.com", region.OpenBaseURL + "/proxy", region.OpenBaseURL + "?host=other", region.OpenBaseURL + "//"} {
				config.Credentials = map[string]interface{}{"base_url": baseURL}
				if _, err := newStrictWikiScope(config, region); err == nil {
					t.Fatalf("untrusted origin %v accepted", baseURL)
				}
			}
		})
	}
}
