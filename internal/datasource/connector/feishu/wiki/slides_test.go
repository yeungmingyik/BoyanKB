package wiki

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/Tencent/WeKnora/internal/datasource/connector/feishu/core"
)

const exampleSlides = `<presentation xmlns="http://www.larkoffice.com/sml/2.0">
  <title>title metadata</title>
  <slide><data><shape><content><p>示例 <strong>标题</strong></p></content></shape></data></slide>
  <slide><data><img src="private-image-token"/></data><note><content><p>private note</p></content></note></slide>
  <slide><data><table><tr><td><content><ul><li><p>表格<br/>内容</p></li></ul></content></td></tr></table></data></slide>
</presentation>`

const blankSlides = `<presentation xmlns="https://www.larkoffice.com/sml/2.0"><slide><style/><data/><note><content/></note></slide></presentation>`

func TestSlidesMarkdown(t *testing.T) {
	got, err := slidesMarkdown(exampleSlides)
	want := "## 第 1 页\n\n示例 标题\n\n## 第 3 页\n\n表格\n内容"
	if err != nil || got != want {
		t.Fatalf("got %q, %v; want %q", got, err, want)
	}
	if content, err := slidesMarkdown(blankSlides); err != nil || content != "" {
		t.Fatalf("blank slide: got %q, %v", content, err)
	}
	for _, sml := range []string{"<presentation>", "<other/>", "<presentation><slide><data><img/></data></slide></presentation>"} {
		if _, err := slidesMarkdown(sml); err == nil {
			t.Fatalf("invalid or unindexable XML accepted: %s", sml)
		}
	}
}

func TestSlidesDeniedExportDoesNotReadContent(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/open-apis/auth/v3/tenant_access_token/internal", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, core.TokenResponse{TenantAccessToken: "fake", Expire: 7200})
	})
	mux.HandleFunc("/open-apis/drive/v1/permissions/object/members/auth", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("type") != "slides" {
			t.Error("wrong permission object type")
		}
		writeJSON(w, map[string]any{"code": 0, "data": map[string]bool{"auth_result": r.URL.Query().Get("action") == "view"}})
	})
	mux.HandleFunc("/open-apis/slides_ai/", func(w http.ResponseWriter, r *http.Request) { t.Error("read a non-exportable deck") })
	server := httptest.NewServer(mux)
	defer server.Close()
	client := core.NewClient(&core.Config{AppID: "app", AppSecret: "fake", BaseURL: server.URL})
	_, err := client.ReadExportableSlides(context.Background(), "object")
	if err == nil || !strings.Contains(err.Error(), "export permission") {
		t.Fatalf("expected export denial, got %v", err)
	}
}

func TestSlidesBackfillsLegacyCursorOnceThenTracksEdits(t *testing.T) {
	node := core.WikiNode{NodeToken: "wiki-node", ObjToken: "initially-blank", Title: "演示", ObjType: "slides", ObjEditTime: "100"}
	nodes := []core.WikiNode{node}
	server, cfg := fakeFeishu(nodes)
	defer server.Close()
	c := NewConnector(core.RegionFeishu)
	config := makeConfig(cfg, []string{"space1"})
	cursor := makeStreamCursor(t, map[string]map[string]string{"space1": {"wiki-node": "100"}})
	for _, step := range []struct {
		edited string
		emits  int
	}{{"100", 1}, {"100", 0}, {"101", 1}} {
		nodes[0].ObjEditTime = step.edited
		h := &recordingHandler{}
		var err error
		cursor, err = c.FetchStream(context.Background(), config, cursor, h)
		if err != nil || len(h.emitted) != step.emits {
			t.Fatalf("edit time %s: %d items, %v", step.edited, len(h.emitted), err)
		}
		if step.emits != 0 {
			item := h.emitted[0]
			wantURL := core.RegionFeishu.WikiURL("wiki-node")
			wantText := "示例"
			if step.edited == "100" {
				wantText = "原稿为空白"
			}
			if item.ExternalID != "wiki-node" || item.URL != wantURL || item.Metadata["source_url"] != wantURL ||
				(item.Metadata["source_empty"] == "true") != (step.edited == "100") ||
				!strings.HasSuffix(string(item.Content), "[飞书原稿]("+wantURL+")") ||
				!strings.Contains(string(item.Content), wantText) || !item.UpdatedAt.Equal(core.ParseFeishuTimestamp(step.edited)) {
				t.Fatalf("unexpected presentation identity, version or content: %+v", item)
			}
		}
	}
}
