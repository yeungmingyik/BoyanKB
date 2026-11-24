package anydoc

import (
	"archive/zip"
	"bytes"
	"errors"
	"io"
	"path"
	"strings"
)

// Build a bounded, in-memory package for the existing Word parser. Keep the
// document, styles, relationships and other Word text parts, without opening
// the embedded media which exhausted AnyDoc's budget. The original is retained.
func docxWithoutMedia(data []byte) ([]byte, error) {
	z, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, err
	}
	if len(z.File) > 10000 {
		return nil, errors.New("too many Word parts")
	}
	var output bytes.Buffer
	w := zip.NewWriter(&output)
	seen := make(map[string]bool)
	const maxPart = 8 << 20
	remaining := int64(32 << 20)
	for _, f := range z.File {
		name := f.Name
		if name != "[Content_Types].xml" && name != "_rels/.rels" && !(path.Dir(name) == "word" && path.Ext(name) == ".xml") &&
			!(path.Dir(name) == "word/_rels" && strings.HasSuffix(name, ".rels")) {
			continue
		}
		if seen[name] || f.UncompressedSize64 > uint64(min(maxPart, remaining)) {
			return nil, errors.New("duplicate or oversized Word text part")
		}
		seen[name] = true
		dst, err := w.CreateHeader(&zip.FileHeader{Name: name, Method: zip.Store})
		if err != nil {
			return nil, err
		}
		src, err := f.Open()
		if err != nil {
			return nil, err
		}
		n, err := io.Copy(dst, io.LimitReader(src, int64(f.UncompressedSize64)+1))
		src.Close()
		if err != nil || uint64(n) != f.UncompressedSize64 {
			return nil, errors.New("unreadable Word text part")
		}
		remaining -= n
	}
	if !seen["word/document.xml"] || !seen["[Content_Types].xml"] {
		return nil, errors.New("missing Word package parts")
	}
	if err := w.Close(); err != nil {
		return nil, err
	}
	return output.Bytes(), nil
}
