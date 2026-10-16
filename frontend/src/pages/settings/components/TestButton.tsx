/**
 * TestButton — pill button that tests a service connection.
 * Shows testing/success/error states inline.
 */

import { useState, useCallback } from 'react';
import { cn } from '@/utils/cn';

interface TestButtonProps {
  label?: string;
  onTest: () => Promise<{ success: boolean; message: string }>;
}

type Status = 'idle' | 'testing' | 'success' | 'error';

export function TestButton({ label = 'Test Connection', onTest }: TestButtonProps) {
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState('');

  const handleTest = useCallback(async () => {
    setStatus('testing');
    setMessage('');
    try {
      const result = await onTest();
      setStatus(result.success ? 'success' : 'error');
      setMessage(result.message);
    } catch (e) {
      setStatus('error');
      setMessage((e as Error).message);
    }
  }, [onTest]);

  return (
    <div className="flex items-center gap-3 flex-wrap">
      <button
        type="button"
        onClick={handleTest}
        disabled={status === 'testing'}
        className={cn(
          'inline-flex items-center gap-2 rounded-xl px-4 py-2.5 text-[13px] font-semibold transition-colors min-h-[44px]',
          'bg-indigo-500/12 text-indigo-400 border border-indigo-500/20 hover:bg-indigo-500/20',
          status === 'testing' && 'opacity-60 cursor-wait',
        )}
      >
        {status === 'testing' ? (
          <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <circle cx="12" cy="12" r="10" strokeDasharray="50" strokeDashoffset="20" strokeLinecap="round" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
          </svg>
        )}
        {status === 'testing' ? 'Testing...' : label}
      </button>

      {status !== 'idle' && status !== 'testing' && message && (
        <span
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[11px] font-medium',
            status === 'success' && 'bg-green-500/10 text-green-400',
            status === 'error' && 'bg-red-500/10 text-red-400',
          )}
        >
          {status === 'success' ? (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="20 6 9 17 4 12" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          )}
          {message}
        </span>
      )}
    </div>
  );
}
