import { useEffect, useMemo } from 'react';
import { atom, useAtom } from 'jotai';
import { Button } from '@librechat/client';
import { Download, File } from 'lucide-react';
import { QueryKeys } from 'librechat-data-provider';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { KnowledgeAsset as Asset } from 'librechat-data-provider';
import { fetchKnowledgeAsset, useKnowledgeAsset } from './queries';
import { KnowledgeError, KnowledgeLoading } from './Status';
import { getResponseStatus } from '~/utils/errors';
import { useLocalize } from '~/hooks';

const imageTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/avif']);

export default function KnowledgeAsset({ asset, caption }: { asset: Asset; caption?: string }) {
  const localize = useLocalize();
  const queryClient = useQueryClient();
  const preview = asset.type === 'image' && imageTypes.has(asset.mimeType.toLowerCase());
  const query = useKnowledgeAsset(asset.id, preview);
  const [imageURL, setImageURL] = useAtom(useMemo(() => atom<string | null>(null), []));
  const download = useMutation({
    mutationFn: () => fetchKnowledgeAsset(asset.id),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = asset.name.replace(/[\\/:*?"<>|]/g, '_');
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
    onError: (error) => {
      if (getResponseStatus(error) === 403) {
        void queryClient.invalidateQueries([QueryKeys.knowledgeAccess]);
      }
    },
  });

  useEffect(() => {
    if (!query.data || query.isError || !imageTypes.has(query.data.type.toLowerCase())) {
      setImageURL(null);
      return;
    }
    const url = URL.createObjectURL(query.data);
    setImageURL(url);
    return () => URL.revokeObjectURL(url);
  }, [query.data, query.isError, setImageURL]);

  return (
    <figure className="my-4 overflow-hidden rounded-lg border border-border-light">
      {preview && query.isLoading && <KnowledgeLoading />}
      {preview && query.isError && (
        <KnowledgeError
          error={query.error}
          retry={() => void query.refetch()}
          retrying={query.isFetching}
        />
      )}
      {preview && imageURL && !query.isError && (
        <img
          src={imageURL}
          alt={caption || asset.name}
          className="mx-auto max-h-[70vh] max-w-full object-contain"
        />
      )}
      <figcaption className="flex items-center justify-between gap-3 bg-surface-secondary p-3 text-sm">
        <span className="flex min-w-0 items-center gap-2 text-text-secondary">
          <File className="size-4 shrink-0" aria-hidden="true" />
          <span className="break-words">{asset.name}</span>
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={download.isLoading}
          onClick={() => download.mutate()}
          aria-label={`${localize('com_ui_download')} ${asset.name}`}
        >
          <Download className="mr-2 size-4" aria-hidden="true" />
          {localize('com_ui_download')}
        </Button>
      </figcaption>
      {download.isError && <KnowledgeError error={download.error} />}
    </figure>
  );
}
