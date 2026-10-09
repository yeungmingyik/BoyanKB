package businessworkflow

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestActualPPTV2ArtifactsKeepReviewEvidence(t *testing.T) {
	root := os.Getenv("BOYAN_PPT_V2_FIXTURES_DIR")
	if root == "" {
		t.Skip("BOYAN_PPT_V2_FIXTURES_DIR is unset")
	}
	for _, scene := range []string{"teaching", "business", "operations"} {
		t.Run(scene, func(t *testing.T) {
			name := scene + ".pptx"
			data, err := os.ReadFile(filepath.Join(root, name))
			if err != nil {
				t.Fatal(err)
			}
			artifact := Artifact{Filename: name, MimeType: MimeTypes["pptx"], Bytes: data, Size: len(data), SHA256: digest(data)}
			if err := artifact.Validate("pptx"); err != nil {
				t.Fatalf("actual Office container rejected: %v", err)
			}
			text, err := artifactText(data, "pptx")
			if err != nil {
				t.Fatal(err)
			}
			for _, value := range []string{"TeachingQualityChecklist2026", "45分钟", "1,480.50元", "核对任务18", "来源附加备注也必须保留", "template-test-source"} {
				if !strings.Contains(text, value) {
					t.Fatalf("missing review evidence %q", value)
				}
			}
		})
	}
}
