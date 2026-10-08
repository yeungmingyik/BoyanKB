package branding

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"regexp"
	"strings"
	"sync/atomic"
	"unicode"
)

type Links struct {
	Home       string `json:"home"`
	Help       string `json:"help"`
	Repository string `json:"repository"`
	Feedback   string `json:"feedback"`
}

type Config struct {
	ProductName string `json:"productName"`
	ProductSlug string `json:"productSlug"`
	Description string `json:"description"`
	Links       Links  `json:"links"`
	configured  bool
}

var current atomic.Pointer[Config]

var slugPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
var productPattern = regexp.MustCompile(`\bWeKnora(?: Cloud\b| 云服务|云服务|\b)`)
var identityPattern = regexp.MustCompile(`(?m)^You are (?:the )?WeKnora\b[^\r\n]*`)

func Default() Config {
	return Config{
		ProductName: "WeKnora",
		ProductSlug: "weknora",
		Description: "WeKnora 知识库管理系统 API 文档",
		Links:       Links{Repository: "https://github.com/Tencent/WeKnora"},
	}
}

func Load(path string) (Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("read branding config: %w", err)
	}
	var cfg Config
	if err := json.Unmarshal(data, &cfg); err != nil {
		return Config{}, fmt.Errorf("decode branding config: %w", err)
	}
	if cfg.ProductName == "" || strings.TrimSpace(cfg.ProductName) != cfg.ProductName || len([]rune(cfg.ProductName)) > 120 {
		return Config{}, fmt.Errorf("branding productName must contain 1-120 characters without surrounding whitespace")
	}
	for _, r := range cfg.ProductName {
		if unicode.IsControl(r) {
			return Config{}, fmt.Errorf("branding productName must not contain control characters")
		}
	}
	if len(cfg.ProductSlug) > 80 || !slugPattern.MatchString(cfg.ProductSlug) {
		return Config{}, fmt.Errorf("branding productSlug must contain lowercase letters, digits, or separating hyphens")
	}
	cfg.configured = true
	return cfg, nil
}

func LoadFromEnv() error {
	path := strings.TrimSpace(os.Getenv("BRANDING_CONFIG"))
	explicit := path != ""
	if !explicit {
		path = "branding/brand.json"
	}
	cfg, err := Load(path)
	if err != nil {
		if !explicit && errors.Is(err, os.ErrNotExist) {
			cfg = Default()
		} else {
			return err
		}
	}
	current.Store(&cfg)
	return nil
}

func Current() Config {
	if cfg := current.Load(); cfg != nil {
		return *cfg
	}
	return Default()
}

func Name() string {
	return Current().ProductName
}

func Slug() string {
	return Current().ProductSlug
}

func ProductText(text string) string {
	return Current().ProductText(text)
}

func (c Config) ProductText(text string) string {
	if !c.configured {
		return text
	}
	var out strings.Builder
	last := 0
	for _, loc := range productPattern.FindAllStringIndex(text, -1) {
		out.WriteString(text[last:loc[0]])
		match := text[loc[0]:loc[1]]
		if match != "WeKnora" || loc[0] > 0 && strings.ContainsRune("/-_.@", rune(text[loc[0]-1])) {
			out.WriteString(match)
		} else {
			out.WriteString(c.ProductName)
		}
		last = loc[1]
	}
	out.WriteString(text[last:])
	return out.String()
}

func (c Config) PromptText(text string) string {
	if !c.configured {
		return text
	}
	return identityPattern.ReplaceAllStringFunc(text, func(identity string) string {
		identity = strings.Replace(identity, "WeKnora", c.ProductName, 1)
		return strings.ReplaceAll(identity, " developed by Tencent", "")
	})
}
