import { useQuery } from '@tanstack/react-query';
import { Button, Spinner } from '@librechat/client';
import { QueryKeys, dataService } from 'librechat-data-provider';
import type { ReactNode } from 'react';
import { useGetStartupConfig } from '~/data-provider';
import { useAuthContext } from '~/hooks/AuthContext';
import { PRODUCT_NAME } from '~/common/product';
import { useLocalize } from '~/hooks';

export default function KnowledgeAccessGate({ children }: { children: ReactNode }) {
  const localize = useLocalize();
  const { isAuthenticated, user, logout } = useAuthContext();
  const startup = useGetStartupConfig();
  const enabled = startup.data?.knowledge?.enabled === true;
  const access = useQuery(
    [QueryKeys.knowledgeAccess, user?.id, startup.data?.knowledge?.agentId],
    () => dataService.getKnowledgeAccess(),
    {
      enabled: isAuthenticated && enabled,
      retry: false,
      cacheTime: 0,
      staleTime: 0,
      refetchInterval: 30_000,
      refetchOnWindowFocus: true,
    },
  );

  if (!isAuthenticated) {
    return null;
  }

  if (startup.data && !enabled) {
    return <>{children}</>;
  }

  if (!startup.isError && !access.isError && enabled && access.data?.access === true) {
    return <>{children}</>;
  }

  const loading =
    (!startup.data && !startup.isError) || (enabled && !access.data && !access.isError);
  const accessMessage =
    access.data?.configured === false ? 'com_knowledge_unavailable' : 'com_knowledge_access_denied';
  const message = startup.isError || access.isError ? 'com_knowledge_access_failed' : accessMessage;

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-surface-primary p-6 text-center text-text-primary">
      <h1 className="text-2xl font-semibold">{startup.data?.appTitle || PRODUCT_NAME}</h1>
      {loading ? (
        <div role="status" aria-label={localize('com_ui_loading')}>
          <Spinner className="text-text-primary" />
        </div>
      ) : (
        <>
          <p role="status">{localize(message)}</p>
          <div className="flex flex-wrap justify-center gap-3">
            <Button
              variant="outline"
              disabled={startup.isFetching || access.isFetching}
              onClick={() => void (startup.isError ? startup.refetch() : access.refetch())}
            >
              {localize('com_ui_retry')}
            </Button>
            <Button variant="outline" onClick={() => logout('/login?redirect=false')}>
              {localize('com_nav_log_out')}
            </Button>
          </div>
        </>
      )}
    </main>
  );
}
