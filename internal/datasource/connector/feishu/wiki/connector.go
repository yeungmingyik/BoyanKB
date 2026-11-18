package wiki

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"

	"github.com/Tencent/WeKnora/internal/datasource"
	"github.com/Tencent/WeKnora/internal/datasource/connector/feishu/core"
	"github.com/Tencent/WeKnora/internal/logger"
	"github.com/Tencent/WeKnora/internal/types"
)

// Connector implements the datasource.Connector interface for Feishu and, with
// the same code, for Lark: the two clouds expose an identical wiki/docx/drive
// API surface. A core.Region picks the cloud — see region.go.
type Connector struct {
	region core.Region
}

// NewConnector creates a connector for the given region (RegionFeishu or RegionLark).
func NewConnector(region core.Region) *Connector {
	return &Connector{region: region}
}

// Feishu supports resumable streaming sync; the service prefers FetchStream over
// FetchAll/FetchIncremental when a connector implements StreamingConnector.
var _ datasource.StreamingConnector = (*Connector)(nil)

// Type returns the connector type identifier.
func (c *Connector) Type() string {
	return c.region.ConnectorType
}

// Validate verifies that the Feishu configuration is valid by testing connectivity.
func (c *Connector) Validate(ctx context.Context, config *types.DataSourceConfig) error {
	feishuConfig, err := core.ParseFeishuConfig(config, c.region)
	if err != nil {
		return err
	}
	if _, err := newStrictWikiScope(config, c.region); err != nil {
		return err
	}

	client := core.NewClient(feishuConfig)
	if err := client.Ping(ctx); err != nil {
		return fmt.Errorf("feishu connection failed: %w", err)
	}

	return nil
}

// ListResources lists Feishu Wiki resources for selection, loading the tree
// lazily one level at a time to avoid traversing the entire wiki up front.
//
//   - parentID == ""        → list all accessible wiki spaces.
//   - parentID == spaceID   → list the top-level nodes of that space.
//   - parentID == "spaceID:nodeToken" → list the direct children of that node.
//
// Eagerly recursing the whole tree here used to time out for large wikis
// (Tencent/WeKnora#1672); the recursive walk now happens only at sync time.
func (c *Connector) ListResources(
	ctx context.Context, config *types.DataSourceConfig, parentID string,
) ([]types.Resource, error) {
	feishuConfig, err := core.ParseFeishuConfig(config, c.region)
	if err != nil {
		return nil, err
	}

	client := core.NewClient(feishuConfig)

	scope, err := newStrictWikiScope(config, c.region)
	if err != nil {
		return nil, err
	}
	if scope != nil {
		return c.listStrictResources(ctx, client, scope, parentID)
	}

	if parentID == "" {
		spaces, err := client.ListWikiSpaces(ctx)
		if err != nil {
			return nil, fmt.Errorf("list feishu wiki spaces: %w", err)
		}

		resources := make([]types.Resource, 0, len(spaces))
		for _, space := range spaces {
			resources = append(resources, types.Resource{
				ExternalID:  space.SpaceID,
				Name:        space.Name,
				Type:        "wiki_space",
				Description: space.Description,
				URL:         c.region.WikiURL(space.SpaceID),
				HasChildren: true,
				Metadata: map[string]interface{}{
					"visibility": space.Visibility,
					"space_id":   space.SpaceID,
				},
			})
		}
		return resources, nil
	}

	// Lazy load: list only the direct children of the given space / node.
	spaceID, nodeToken := parseWikiResourceID(parentID)
	nodes, err := client.ListWikiNodes(ctx, spaceID, nodeToken)
	if err != nil {
		return nil, fmt.Errorf("list feishu wiki nodes under %s: %w", parentID, err)
	}

	resources := make([]types.Resource, 0, len(nodes))
	for _, node := range nodes {
		resources = append(resources, c.wikiNodeToResource(spaceID, node))
	}
	return resources, nil
}

// ResolveResourceAncestors returns the resource IDs of every parent that has to
// be expanded so the lazily-loaded picker can reveal each given selection. For a
// selected node "spaceID:nodeToken" that is its space plus every intermediate
// node up the tree; the walk uses GetWikiNode (parent_node_token) and is O(depth)
// per selection, so it never re-traverses the whole wiki.
func (c *Connector) ResolveResourceAncestors(
	ctx context.Context, config *types.DataSourceConfig, resourceIDs []string,
) ([]string, error) {
	feishuConfig, err := core.ParseFeishuConfig(config, c.region)
	if err != nil {
		return nil, err
	}
	client := core.NewClient(feishuConfig)
	scope, err := newStrictWikiScope(config, c.region)
	if err != nil {
		return nil, err
	}
	if scope != nil {
		return scope.ancestors(ctx, client, resourceIDs)
	}

	seen := make(map[string]bool)
	ancestors := make([]string, 0)
	add := func(id string) {
		if id != "" && !seen[id] {
			seen[id] = true
			ancestors = append(ancestors, id)
		}
	}

	for _, rid := range resourceIDs {
		spaceID, nodeToken := parseWikiResourceID(rid)
		if spaceID == "" || nodeToken == "" {
			// A space-level selection is already a top-level node in the picker;
			// there is nothing above it to reveal.
			continue
		}
		// The space's direct children must be loaded to reveal the top-level node.
		add(spaceID)

		// Walk up from the selection to the top, loading each intermediate
		// parent so the path down to the selection becomes visible.
		current := nodeToken
		for current != "" {
			node, err := client.GetWikiNode(ctx, spaceID, current)
			if err != nil {
				// Best-effort: a broken path just stays collapsed, the rest of
				// the selections are still revealed.
				logger.Warnf(ctx, "[Feishu] resolve ancestors: get node %s:%s: %v", spaceID, current, err)
				break
			}
			if node.ParentNodeID == "" {
				break
			}
			add(makeWikiNodeResourceID(spaceID, node.ParentNodeID))
			current = node.ParentNodeID
		}
	}

	return ancestors, nil
}

// FetchAll performs a full sync of all documents from the specified wiki spaces.
// Defensive fallback path - the service prefers FetchStream when the connector
// implements StreamingConnector.
func (c *Connector) FetchAll(ctx context.Context, config *types.DataSourceConfig, resourceIDs []string) ([]types.FetchedItem, error) {
	feishuConfig, err := core.ParseFeishuConfig(config, c.region)
	if err != nil {
		return nil, err
	}
	client := core.NewClient(feishuConfig)
	ops, err := c.prepareWikiOps(ctx, client, config, resourceIDs)
	if err != nil {
		return nil, err
	}
	return core.FetchAllEngine(ctx, client, config, resourceIDs, ops)
}

// FetchIncremental performs an incremental sync by comparing node edit times
// against the previously recorded state. Defensive fallback path - the service
// prefers FetchStream. Routed through the same engine, so the #2136
// failure-doesn't-advance-cursor semantics apply here too (previously this path
// advanced the cursor before fetching, a latent #2136 bug).
func (c *Connector) FetchIncremental(ctx context.Context, config *types.DataSourceConfig, cursor *types.SyncCursor) ([]types.FetchedItem, *types.SyncCursor, error) {
	feishuConfig, err := core.ParseFeishuConfig(config, c.region)
	if err != nil {
		return nil, nil, err
	}
	client := core.NewClient(feishuConfig)
	ops, err := c.prepareWikiOps(ctx, client, config, config.ResourceIDs)
	if err != nil {
		return nil, nil, err
	}
	if len(config.ResourceIDs) == 0 {
		return nil, nil, errors.New(ops.EmptyResourceIDsError())
	}
	return core.FetchIncrementalEngine(ctx, client, config, cursor, ops)
}

// FetchStream performs a resumable, memory-bounded sync. It unifies the full
// and incremental paths: with cursor == nil it fetches everything, and with a
// cursor it skips nodes whose recorded edit time is unchanged - the same
// mechanism that lets a sync which timed out mid-traversal resume from the last
// checkpoint instead of restarting (Tencent/WeKnora#2136).
//
// The per-node loop lives in the shared engine (engine.go); this shell only
// wires the wiki NodeOps adapter.
func (c *Connector) FetchStream(
	ctx context.Context, config *types.DataSourceConfig,
	cursor *types.SyncCursor, h datasource.StreamHandler,
) (*types.SyncCursor, error) {
	feishuConfig, err := core.ParseFeishuConfig(config, c.region)
	if err != nil {
		return nil, err
	}
	client := core.NewClient(feishuConfig)
	ops, err := c.prepareWikiOps(ctx, client, config, config.ResourceIDs)
	if err != nil {
		return nil, err
	}
	if len(config.ResourceIDs) == 0 {
		return nil, errors.New(ops.EmptyResourceIDsError())
	}
	return core.FetchStreamEngine(ctx, client, config, cursor, h, ops)
}

// wikiOps adapts the wiki Connector to the generic sync engine. It carries the
// region (for URL rendering) and encodes/decodes the wiki cursor wire format
// (core.FeishuCursor / space_node_times) so the engine can stay format-agnostic.
type wikiOps struct {
	region      core.Region
	strictNodes map[string][]core.WikiNode
}

func (o wikiOps) List(ctx context.Context, client *core.Client, resourceID string) ([]core.WikiNode, error, error) {
	if o.strictNodes != nil {
		nodes, ok := o.strictNodes[resourceID]
		if !ok {
			return nil, nil, fmt.Errorf("wiki resource was not preflighted: %s", resourceID)
		}
		return nodes, nil, nil
	}
	spaceID, nodeToken := parseWikiResourceID(resourceID)
	nodes, err := client.ListWikiNodesRecursiveFrom(ctx, spaceID, nodeToken)
	if err == nil {
		return nodes, nil, nil
	}
	var partial *core.PartialWikiNodeListError
	if errors.As(err, &partial) {
		// Partial listing: nodes are still usable; the failed sub-trees are
		// surfaced via ListFailureItems, and the sync continues.
		return nodes, err, nil
	}
	return nodes, nil, err
}

func (o wikiOps) Token(n core.WikiNode) string   { return n.NodeToken }
func (o wikiOps) Title(n core.WikiNode) string   { return n.Title }
func (o wikiOps) ObjType(n core.WikiNode) string { return n.ObjType }

// EditTime builds the cursor comparison value from the document edit time.
// contentEditTime independently retains the source timestamp for ingestion.
func (o wikiOps) EditTime(n core.WikiNode) string {
	edited := n.ObjEditTime
	if edited == "" {
		edited = n.NodeEditTime
	}
	if n.ObjType == "slides" {
		// Legacy cursors recorded skipped slides using the plain timestamp.
		return "sml1:" + edited
	}
	return edited
}

func (o wikiOps) Fetch(ctx context.Context, client *core.Client, n core.WikiNode, resourceID string, multimodal bool) ([]*types.FetchedItem, error) {
	spaceID, _ := parseWikiResourceID(resourceID)
	return fetchNodeContent(ctx, client, n, spaceID, resourceID, multimodal, o.region)
}

func (o wikiOps) ListFailureItems(resourceID string, partial error) []types.FetchedItem {
	spaceID, _ := parseWikiResourceID(resourceID)
	var pe *core.PartialWikiNodeListError
	if errors.As(partial, &pe) {
		return appendWikiNodeListFailureItems(nil, spaceID, resourceID, pe.Failures)
	}
	return nil
}

func (o wikiOps) ResourceNoun() string { return "nodes" }
func (o wikiOps) EmptyResourceIDsError() string {
	return "no resource IDs (wiki space IDs or wiki node IDs) configured"
}
func (o wikiOps) LogTag() string { return "[Feishu]" }

func (o wikiOps) DecodeCursorTimes(m map[string]interface{}) map[string]map[string]string {
	var prev core.FeishuCursor
	b, _ := json.Marshal(m)
	_ = json.Unmarshal(b, &prev)
	return prev.SpaceNodeTimes
}

func (o wikiOps) EncodeCursor(times map[string]map[string]string, lastSync time.Time) *types.SyncCursor {
	fc := core.FeishuCursor{LastSyncTime: lastSync, SpaceNodeTimes: times}
	m := make(map[string]interface{})
	b, _ := json.Marshal(fc)
	_ = json.Unmarshal(b, &m)
	return &types.SyncCursor{LastSyncTime: lastSync, ConnectorCursor: m}
}

func appendWikiNodeListFailureItems(items []types.FetchedItem, spaceID string, resourceID string, failures []core.WikiNodeListFailure) []types.FetchedItem {
	for _, failure := range failures {
		node := failure.Node
		title := node.Title
		if title == "" {
			title = node.NodeToken
		}
		items = append(items, types.FetchedItem{
			ExternalID:       node.NodeToken,
			Title:            title,
			SourceResourceID: resourceID,
			Metadata: core.FeishuErrorItemMeta(failure.Err, map[string]string{
				"channel":       types.ChannelFeishu,
				"node_token":    node.NodeToken,
				"space_id":      spaceID,
				"failure_stage": "list_children",
			}),
		})
	}
	return items
}

// fetchNodeContent fetches the content of a single wiki node and converts it to a
// slice of FetchedItems. For docx nodes it fans out into a main Markdown document
// plus optional attachment sub-items. Dispatches to different retrieval strategies
// based on obj_type:
//   - docx       → blocks API (Markdown) with export fallback; may return attachments
//   - doc/sheet/bitable → export API → binary file
//   - file       → drive download → original file (PDF/Word/image/etc.)
//   - mindnote   → Skip (no API)
//   - slides     → visible text in page order (Feishu SML)
func fetchNodeContent(ctx context.Context, client *core.Client, node core.WikiNode, spaceID string, resourceID string, multimodalEnabled bool, region core.Region) ([]*types.FetchedItem, error) {
	if node.ObjType != "slides" && !core.IsSupportedDocType(node.ObjType) {
		return nil, nil
	}

	editTime := contentEditTime(node)
	createTime := contentCreateTime(node)
	baseMeta := map[string]string{
		"obj_token":  node.ObjToken,
		"obj_type":   node.ObjType,
		"node_token": node.NodeToken,
		"space_id":   spaceID,
		"creator":    node.Creator,
		"owner":      node.Owner,
		"channel":    types.ChannelFeishu,
	}

	switch node.ObjType {
	case "slides":
		xmlContent, err := client.ReadExportableSlides(ctx, node.ObjToken)
		if err != nil {
			return nil, err
		}
		content, err := slidesMarkdown(xmlContent)
		if err != nil {
			return nil, err
		}
		return []*types.FetchedItem{{
			ExternalID: node.NodeToken, Title: node.Title, Content: []byte(content),
			ContentType: "text/markdown", FileName: core.SanitizeFileName(node.Title) + ".md",
			URL: region.WikiURL(node.NodeToken), SourceResourceID: resourceID,
			UpdatedAt: editTime, CreatedAt: createTime, Metadata: baseMeta,
		}}, nil
	case "docx":
		return core.FetchDocxWithBlocks(ctx, client, core.DocxFetchInput{
			DocToken:          node.NodeToken,
			ObjToken:          node.ObjToken,
			Title:             node.Title,
			URL:               region.WikiURL(node.NodeToken),
			ResourceID:        resourceID,
			EditTime:          editTime,
			CreateTime:        createTime,
			BaseMeta:          baseMeta,
			MultimodalEnabled: multimodalEnabled,
		})
	case "doc", "sheet", "bitable":
		item, err := fetchViaExport(ctx, client, node, resourceID, editTime, baseMeta, region)
		if err != nil {
			return nil, err
		}
		item.CreatedAt = createTime
		return []*types.FetchedItem{item}, nil
	case "file":
		item, err := fetchDriveFile(ctx, client, node, resourceID, editTime, baseMeta, region)
		if err != nil {
			return nil, err
		}
		item.CreatedAt = createTime
		return []*types.FetchedItem{item}, nil
	default:
		return nil, nil
	}
}

// fetchViaExport exports a doc/sheet/bitable node via the async export API and
// returns a single FetchedItem containing the exported binary.
func fetchViaExport(ctx context.Context, client *core.Client, node core.WikiNode, resourceID string, editTime time.Time, baseMeta map[string]string, region core.Region) (*types.FetchedItem, error) {
	// Export as a file via the async export API
	data, fileName, err := client.ExportAndDownload(ctx, node.ObjToken, node.ObjType)
	if err != nil {
		return nil, fmt.Errorf("export %s (%s): %w", node.Title, node.ObjType, err)
	}

	// Ensure a reasonable file name with correct extension
	ext := core.ExportFileExtToSuffix[core.ObjTypeToExportFileExtension[node.ObjType]]
	if fileName == "" {
		fileName = core.SanitizeFileName(node.Title) + ext
	} else if !strings.HasSuffix(strings.ToLower(fileName), ext) {
		// Feishu often returns the doc title without extension - append it
		fileName = core.SanitizeFileName(fileName) + ext
	}

	return &types.FetchedItem{
		ExternalID:       node.NodeToken,
		Title:            node.Title,
		Content:          data,
		ContentType:      "application/octet-stream",
		FileName:         fileName,
		URL:              region.WikiURL(node.NodeToken),
		UpdatedAt:        editTime,
		SourceResourceID: resourceID,
		Metadata:         baseMeta,
	}, nil
}

// fetchDriveFile downloads an original uploaded file from Drive and returns a
// single FetchedItem containing the raw bytes.
func fetchDriveFile(ctx context.Context, client *core.Client, node core.WikiNode, resourceID string, editTime time.Time, baseMeta map[string]string, region core.Region) (*types.FetchedItem, error) {
	// Download the original uploaded file from Drive
	data, err := client.DownloadDriveFile(ctx, node.ObjToken)
	if err != nil {
		return nil, fmt.Errorf("download file %s (%s): %w", node.Title, node.ObjToken, err)
	}

	// Use the node title as file name; it usually preserves the original extension
	fileName := node.Title
	if fileName == "" {
		fileName = node.ObjToken
	}

	return &types.FetchedItem{
		ExternalID:       node.NodeToken,
		Title:            node.Title,
		Content:          data,
		ContentType:      "application/octet-stream",
		FileName:         fileName,
		URL:              region.WikiURL(node.NodeToken),
		UpdatedAt:        editTime,
		SourceResourceID: resourceID,
		Metadata:         baseMeta,
	}, nil
}

// contentEditTime is the document's last content edit time (obj_edit_time),
// falling back to the node attribute edit time when Feishu omits it. It is
// what ingestion persists as source_updated_at; NodeEditTime alone would move
// on a rename or a move in the tree without any content change.
func contentEditTime(node core.WikiNode) time.Time {
	if t := core.ParseFeishuTimestamp(node.ObjEditTime); !t.IsZero() {
		return t
	}
	return core.ParseFeishuTimestamp(node.NodeEditTime)
}

// contentCreateTime is the document creation time (obj_create_time), falling
// back to the node creation time. Persisted as source_created_at.
func contentCreateTime(node core.WikiNode) time.Time {
	if t := core.ParseFeishuTimestamp(node.ObjCreateTime); !t.IsZero() {
		return t
	}
	return core.ParseFeishuTimestamp(node.NodeCreateTime)
}

// --- Helper functions ---

func makeWikiNodeResourceID(spaceID, nodeToken string) string {
	return spaceID + core.FeishuWikiNodeResourceSeparator + nodeToken
}

func parseWikiResourceID(resourceID string) (spaceID string, nodeToken string) {
	spaceID, nodeToken, _ = strings.Cut(resourceID, core.FeishuWikiNodeResourceSeparator)
	return spaceID, nodeToken
}

type strictWikiScope struct {
	spaceID string
}

func newStrictWikiScope(config *types.DataSourceConfig, region core.Region) (*strictWikiScope, error) {
	value, configured := config.Settings["allowed_space_id"]
	if !configured {
		return nil, nil
	}
	spaceID, ok := value.(string)
	if !ok {
		return nil, errors.New("allowed_space_id must be a string")
	}
	if spaceID == "" {
		return nil, errors.New("allowed_space_id must not be empty")
	}
	if strings.TrimSpace(spaceID) != spaceID || strings.Contains(spaceID, core.FeishuWikiNodeResourceSeparator) {
		return nil, errors.New("invalid allowed_space_id")
	}
	if value, configured := config.Credentials["base_url"]; configured {
		baseURL, ok := value.(string)
		if !ok || strings.TrimSuffix(baseURL, "/") != strings.TrimSuffix(region.OpenBaseURL, "/") {
			return nil, errors.New("allowed_space_id requires the connector region's official base_url")
		}
	}
	mode := strings.TrimSpace(os.Getenv("FEISHU_DOCX_PARSE_MODE"))
	if mode != "" && !strings.EqualFold(mode, "export") {
		return nil, errors.New("allowed_space_id requires FEISHU_DOCX_PARSE_MODE=export")
	}
	scope := &strictWikiScope{spaceID: spaceID}
	if err := scope.validateResources(config.ResourceIDs); err != nil {
		return nil, err
	}
	return scope, nil
}

func (s *strictWikiScope) validateResources(resourceIDs []string) error {
	for _, resourceID := range resourceIDs {
		spaceID, nodeToken, hasNode := strings.Cut(resourceID, core.FeishuWikiNodeResourceSeparator)
		if spaceID != s.spaceID || (hasNode && (nodeToken == "" || strings.Contains(nodeToken, core.FeishuWikiNodeResourceSeparator))) {
			return fmt.Errorf("wiki resource is outside allowed space: %s", resourceID)
		}
	}
	return nil
}

func (s *strictWikiScope) validateNode(node core.WikiNode) error {
	if node.SpaceID != s.spaceID {
		return fmt.Errorf("wiki node %s has unexpected space_id %q", node.NodeToken, node.SpaceID)
	}
	if node.NodeType != "origin" {
		return fmt.Errorf("wiki node %s has disallowed node_type %q", node.NodeToken, node.NodeType)
	}
	if node.OriginSpaceID != "" && node.OriginSpaceID != s.spaceID {
		return fmt.Errorf("wiki node %s has an external origin_space_id", node.NodeToken)
	}
	if node.NodeToken == "" {
		return errors.New("wiki node has an empty node_token")
	}
	if (node.ObjType == "slides" || core.IsSupportedDocType(node.ObjType)) && node.ObjToken == "" {
		return fmt.Errorf("wiki node %s has an empty obj_token", node.NodeToken)
	}
	return nil
}

func (s *strictWikiScope) getNode(ctx context.Context, client *core.Client, nodeToken string) (core.WikiNode, error) {
	var response core.WikiNodeInfoResponse
	path := "/open-apis/wiki/v2/spaces/get_node?token=" + url.QueryEscape(nodeToken)
	if err := client.DoRequest(ctx, http.MethodGet, path, nil, &response); err != nil {
		return core.WikiNode{}, err
	}
	if response.Code != 0 {
		return core.WikiNode{}, fmt.Errorf("get wiki node error: code=%d", response.Code)
	}
	node := response.Data.Node
	if err := s.validateNode(node); err != nil {
		return core.WikiNode{}, err
	}
	if node.NodeToken != nodeToken {
		return core.WikiNode{}, errors.New("wiki node token does not match the requested token")
	}
	return node, nil
}

func (s *strictWikiScope) listNodes(ctx context.Context, client *core.Client, parentNodeToken string) ([]core.WikiNode, error) {
	var nodes []core.WikiNode
	pageToken := ""
	seenPages := make(map[string]bool)
	for {
		query := url.Values{"page_size": {"50"}}
		if parentNodeToken != "" {
			query.Set("parent_node_token", parentNodeToken)
		}
		if pageToken != "" {
			query.Set("page_token", pageToken)
		}
		path := "/open-apis/wiki/v2/spaces/" + url.PathEscape(s.spaceID) + "/nodes?" + query.Encode()
		var response core.WikiNodeListResponse
		if err := client.DoRequest(ctx, http.MethodGet, path, nil, &response); err != nil {
			return nil, err
		}
		if response.Code != 0 {
			return nil, fmt.Errorf("list wiki nodes error: code=%d", response.Code)
		}
		for _, node := range response.Data.Items {
			if err := s.validateNode(node); err != nil {
				return nil, err
			}
			if parentNodeToken != "" && node.ParentNodeID == "" {
				node.ParentNodeID = parentNodeToken
			}
			nodes = append(nodes, node)
		}
		if !response.Data.HasMore {
			return nodes, nil
		}
		pageToken = response.Data.PageToken
		if pageToken == "" || seenPages[pageToken] {
			return nil, errors.New("wiki pagination did not advance")
		}
		seenPages[pageToken] = true
	}
}

func (s *strictWikiScope) preflight(ctx context.Context, client *core.Client, resourceID string) ([]core.WikiNode, error) {
	_, nodeToken := parseWikiResourceID(resourceID)
	var pending []core.WikiNode
	if nodeToken == "" {
		nodes, err := s.listNodes(ctx, client, "")
		if err != nil {
			return nil, err
		}
		pending = nodes
	} else {
		node, err := s.getNode(ctx, client, nodeToken)
		if err != nil {
			return nil, err
		}
		pending = []core.WikiNode{node}
	}
	var nodes []core.WikiNode
	seenNodes := make(map[string]bool)
	for len(pending) > 0 {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		node := pending[0]
		pending = pending[1:]
		if seenNodes[node.NodeToken] {
			return nil, fmt.Errorf("wiki node was listed more than once: %s", node.NodeToken)
		}
		seenNodes[node.NodeToken] = true
		nodes = append(nodes, node)
		if node.HasChild {
			children, err := s.listNodes(ctx, client, node.NodeToken)
			if err != nil {
				return nil, err
			}
			pending = append(pending, children...)
		}
	}
	return nodes, nil
}

func (c *Connector) prepareWikiOps(ctx context.Context, client *core.Client, config *types.DataSourceConfig, resourceIDs []string) (wikiOps, error) {
	ops := wikiOps{region: c.region}
	scope, err := newStrictWikiScope(config, c.region)
	if err != nil || scope == nil {
		return ops, err
	}
	if err := scope.validateResources(resourceIDs); err != nil {
		return ops, err
	}
	ops.strictNodes = make(map[string][]core.WikiNode, len(resourceIDs))
	for _, resourceID := range resourceIDs {
		nodes, err := scope.preflight(ctx, client, resourceID)
		if err != nil {
			return wikiOps{}, fmt.Errorf("wiki scope preflight failed for %s: %w", resourceID, err)
		}
		ops.strictNodes[resourceID] = nodes
	}
	return ops, nil
}

func (c *Connector) listStrictResources(ctx context.Context, client *core.Client, scope *strictWikiScope, parentID string) ([]types.Resource, error) {
	if parentID == "" {
		return []types.Resource{{
			ExternalID:  scope.spaceID,
			Name:        scope.spaceID,
			Type:        "wiki_space",
			URL:         c.region.WikiURL(scope.spaceID),
			HasChildren: true,
			Metadata:    map[string]interface{}{"space_id": scope.spaceID},
		}}, nil
	}
	if err := scope.validateResources([]string{parentID}); err != nil {
		return nil, err
	}
	_, nodeToken := parseWikiResourceID(parentID)
	if nodeToken != "" {
		if _, err := scope.getNode(ctx, client, nodeToken); err != nil {
			return nil, err
		}
	}
	nodes, err := scope.listNodes(ctx, client, nodeToken)
	if err != nil {
		return nil, err
	}
	resources := make([]types.Resource, 0, len(nodes))
	for _, node := range nodes {
		resources = append(resources, c.wikiNodeToResource(scope.spaceID, node))
	}
	return resources, nil
}

func (s *strictWikiScope) ancestors(ctx context.Context, client *core.Client, resourceIDs []string) ([]string, error) {
	if err := s.validateResources(resourceIDs); err != nil {
		return nil, err
	}
	var ancestors []string
	seen := make(map[string]bool)
	add := func(resourceID string) {
		if !seen[resourceID] {
			seen[resourceID] = true
			ancestors = append(ancestors, resourceID)
		}
	}
	for _, resourceID := range resourceIDs {
		_, current := parseWikiResourceID(resourceID)
		if current == "" {
			continue
		}
		add(s.spaceID)
		path := make(map[string]bool)
		for current != "" {
			if path[current] {
				return nil, errors.New("wiki ancestor cycle detected")
			}
			path[current] = true
			node, err := s.getNode(ctx, client, current)
			if err != nil {
				return nil, err
			}
			current = node.ParentNodeID
			if current != "" {
				add(makeWikiNodeResourceID(s.spaceID, current))
			}
		}
	}
	return ancestors, nil
}

func (c *Connector) wikiNodeToResource(spaceID string, node core.WikiNode) types.Resource {
	parentID := spaceID
	if node.ParentNodeID != "" {
		parentID = makeWikiNodeResourceID(spaceID, node.ParentNodeID)
	}

	name := node.Title
	if name == "" {
		name = node.NodeToken
	}

	modifiedAt := core.ParseFeishuTimestamp(node.ObjEditTime)
	if modifiedAt.IsZero() {
		modifiedAt = core.ParseFeishuTimestamp(node.NodeEditTime)
	}

	return types.Resource{
		ExternalID:  makeWikiNodeResourceID(spaceID, node.NodeToken),
		Name:        name,
		Type:        "wiki_node",
		URL:         c.region.WikiURL(node.NodeToken),
		ParentID:    parentID,
		HasChildren: node.HasChild,
		ModifiedAt:  modifiedAt,
		Metadata: map[string]interface{}{
			"space_id":   spaceID,
			"node_token": node.NodeToken,
			"obj_token":  node.ObjToken,
			"obj_type":   node.ObjType,
		},
	}
}
