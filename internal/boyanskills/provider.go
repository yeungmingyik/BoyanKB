package boyanskills

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
	"gorm.io/gorm"
)

const Version = "boyan/v1"

var ErrDenied = errors.New("restricted_skill_access_denied")
var ErrUnavailable = errors.New("restricted_skill_unavailable")

var names = map[string]string{
	"boyan-courseware":           "courseware",
	"boyan-event-plan":           "event-plan",
	"boyan-local-promotion-plan": "local-promotion-plan",
	"boyan-cooperation-proposal": "cooperation-proposal",
	"boyan-faq-guide":            "faq-guide",
	"boyan-docx":                 "docx",
	"boyan-pptx":                 "pptx",
	"boyan-xlsx":                 "xlsx",
	"boyan-md":                   "md",
}

type Guide struct {
	Name         string
	Version      string
	SHA256       string
	Description  string
	Instructions string
	Content      string
}

type skillEntry struct {
	Name    string `json:"name"`
	Key     string `json:"key"`
	Version string `json:"version"`
	SHA256  string `json:"sha256"`
}

type manifest struct {
	SchemaVersion int          `json:"schema_version"`
	PresetVersion string       `json:"preset_version"`
	Skills        []skillEntry `json:"skills"`
}

type installation struct {
	Version   string `json:"version"`
	SHA256    string `json:"sha256"`
	CatalogID string `json:"catalog_id"`
	Enabled   bool   `json:"enabled"`
}

func presetRoot() string {
	if path := os.Getenv("BOYAN_PRESETS_ROOT"); path != "" {
		return path
	}
	return filepath.Join("presets", "boyan", "v1")
}

func skillRoot() string {
	if path := os.Getenv("BOYAN_SKILLS_ROOT"); path != "" {
		return path
	}
	return filepath.Join("skills", "boyan")
}

func readFixed(root, name string) ([]byte, error) {
	base, err := filepath.Abs(root)
	if err != nil {
		return nil, ErrUnavailable
	}
	base, err = filepath.EvalSymlinks(base)
	if err != nil {
		return nil, ErrUnavailable
	}
	path, err := filepath.EvalSymlinks(filepath.Join(base, name))
	if err != nil {
		return nil, ErrUnavailable
	}
	rel, err := filepath.Rel(base, path)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		return nil, ErrDenied
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, ErrUnavailable
	}
	defer f.Close()
	stat, err := f.Stat()
	if err != nil || !stat.Mode().IsRegular() || stat.Size() > 128*1024 {
		return nil, ErrUnavailable
	}
	data, err := io.ReadAll(io.LimitReader(f, 128*1024+1))
	if err != nil || len(data) > 128*1024 {
		return nil, ErrUnavailable
	}
	return data, nil
}

func loadManifest() (manifest, error) {
	data, err := readFixed(presetRoot(), "manifest.json")
	if err != nil {
		return manifest{}, err
	}
	var m manifest
	if json.Unmarshal(data, &m) != nil || m.SchemaVersion != 1 || m.PresetVersion != Version || len(m.Skills) != len(names) {
		return manifest{}, ErrUnavailable
	}
	seen := make(map[string]bool, len(names))
	for _, entry := range m.Skills {
		key, ok := names[entry.Name]
		digest, err := hex.DecodeString(entry.SHA256)
		if !ok || seen[entry.Name] || key != entry.Key || entry.Version != Version || err != nil || len(digest) != 32 || strings.ToLower(entry.SHA256) != entry.SHA256 {
			return manifest{}, ErrUnavailable
		}
		seen[entry.Name] = true
	}
	return m, nil
}

func Fixed(name string) (Guide, error) {
	key, ok := names[name]
	if !ok {
		return Guide{}, ErrDenied
	}
	m, err := loadManifest()
	if err != nil {
		return Guide{}, err
	}
	var expected string
	for _, entry := range m.Skills {
		if entry.Name == name {
			expected = entry.SHA256
		}
	}
	data, err := readFixed(skillRoot(), filepath.Join(key, "SKILL.md"))
	if err != nil {
		return Guide{}, err
	}
	sum := sha256.Sum256(data)
	digest := hex.EncodeToString(sum[:])
	if digest != expected {
		return Guide{}, ErrUnavailable
	}
	content := strings.ReplaceAll(string(data), "\r\n", "\n")
	parts := strings.SplitN(content, "\n---\n", 2)
	if len(parts) != 2 || !strings.HasPrefix(parts[0], "---\n") {
		return Guide{}, ErrUnavailable
	}
	var metadata struct {
		Name        string `yaml:"name"`
		Version     string `yaml:"version"`
		Description string `yaml:"description"`
	}
	if yaml.Unmarshal([]byte(strings.TrimPrefix(parts[0], "---\n")), &metadata) != nil || metadata.Name != name || metadata.Version != Version || strings.TrimSpace(metadata.Description) == "" || strings.TrimSpace(parts[1]) == "" {
		return Guide{}, ErrUnavailable
	}
	return Guide{Name: name, Version: Version, SHA256: digest, Description: metadata.Description, Instructions: strings.TrimSpace(parts[1]), Content: string(data)}, nil
}

func ValidateFixedSkill(name, version, sha string) error {
	guide, err := Fixed(name)
	if err != nil {
		return err
	}
	if version != guide.Version || sha != guide.SHA256 {
		return ErrDenied
	}
	return nil
}

func Read(ctx context.Context, db *gorm.DB, tenantID uint64, name string) (string, error) {
	if db == nil || tenantID == 0 {
		return "", ErrDenied
	}
	if _, ok := names[name]; !ok {
		return "", ErrDenied
	}
	var row struct {
		Status string
		State  []byte
	}
	result := db.WithContext(ctx).Table("partner_provisioning_jobs").Select("status, state").Where("tenant_id = ?", tenantID).Take(&row)
	if result.Error != nil {
		if errors.Is(result.Error, gorm.ErrRecordNotFound) {
			return "", ErrDenied
		}
		return "", ErrUnavailable
	}
	var state struct {
		RestrictedSkills map[string]installation `json:"restricted_skills"`
	}
	if row.Status != "ready" || json.Unmarshal(row.State, &state) != nil {
		return "", ErrDenied
	}
	installed, ok := state.RestrictedSkills[name]
	if !ok || !installed.Enabled || installed.CatalogID == "" {
		return "", ErrDenied
	}
	guide, err := Fixed(name)
	if err != nil {
		return "", err
	}
	if installed.Version != guide.Version || installed.SHA256 != guide.SHA256 {
		return "", ErrDenied
	}
	return guide.Content, nil
}

func ReadAgentPrompt(name string) (string, error) {
	if name != "mentor" && name != "planner" && name != "reviewer" {
		return "", ErrDenied
	}
	paths := []string{"common-rules.md", filepath.Join("agents", name+".md")}
	if name == "planner" {
		paths = append(paths, "workflow-guide.md")
	}
	parts := make([]string, 0, len(paths))
	for _, path := range paths {
		data, err := readFixed(presetRoot(), path)
		if err != nil {
			return "", err
		}
		if strings.TrimSpace(string(data)) == "" {
			return "", ErrUnavailable
		}
		parts = append(parts, strings.TrimSpace(string(data)))
	}
	return strings.Join(parts, "\n\n"), nil
}
