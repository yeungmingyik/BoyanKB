package businessworkflow

import "os"

const templateV1 = "boyan-v1"
const templateV2 = "boyan-v2"

func selectedTemplateVersion(format string) (string, error) {
	if format != "pptx" {
		return templateV1, nil
	}
	version := os.Getenv("BOYAN_PPT_TEMPLATE_VERSION")
	if version == "" {
		return templateV1, nil
	}
	if !supportedTemplateVersion(version, format) {
		return "", ErrUnavailable
	}
	return version, nil
}

func supportedTemplateVersion(version, format string) bool {
	if _, ok := MimeTypes[format]; !ok {
		return false
	}
	return version == templateV1 || (version == templateV2 && format == "pptx")
}
