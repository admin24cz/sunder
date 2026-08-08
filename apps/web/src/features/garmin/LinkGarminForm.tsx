import { type ReactElement, type SubmitEvent, useState } from 'react';

import { supabase } from '@/lib/supabase';

type Status = 'idle' | 'submitting' | 'linked' | 'error';

/** What `link-garmin` returns. It never echoes the submitted password. */
interface LinkResponse {
  ok?: boolean;
  garmin_email?: string;
  error?: string;
}

/**
 * Form for linking a Garmin Connect account (spec 6.5).
 *
 * Two things about this form are security decisions, not styling:
 *
 * 1. It posts to the `link-garmin` Edge Function, never to the database. The
 *    anon key has no access to `garmin_connections` at all, so there is no
 *    direct write path even if this component were altered.
 * 2. The password is held in component state and passed straight to the
 *    function. It is never logged, never put in a query key, and never reaches
 *    TanStack Query's cache — which is why this uses a plain fetch rather than
 *    a mutation with cached variables.
 *
 * The consent checkbox is not decoration either: this is a credential for a
 * third-party account, obtained in a way that breaches Garmin's terms, and the
 * user has to say so explicitly before it is accepted.
 */
export function LinkGarminForm(): ReactElement {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [acceptedRisk, setAcceptedRisk] = useState(false);
  const [status, setStatus] = useState<Status>('idle');
  const [message, setMessage] = useState<string | null>(null);

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    setStatus('submitting');
    setMessage(null);

    try {
      // Destructured through an explicitly typed local rather than inline:
      // `invoke` widens its error channel to `any`, and pulling it apart
      // directly would let an untyped value straight into the component.
      const response: { data: LinkResponse | null; error: unknown } =
        await supabase.functions.invoke<LinkResponse>('link-garmin', {
          body: { garmin_email: email, garmin_password: password, accepted_risk: acceptedRisk },
        });

      if (response.error !== null) {
        // The function's own message when it sent one; a generic fallback
        // otherwise. Neither ever contains the password.
        throw new Error(response.data?.error ?? 'Propojení se nezdařilo.');
      }

      setStatus('linked');
      setMessage('Účet je propojený. Aktivity se stáhnou při nejbližší synchronizaci.');
    } catch (cause) {
      setStatus('error');
      setMessage(cause instanceof Error ? cause.message : 'Propojení se nezdařilo.');
    } finally {
      // Cleared whatever happened, including on success. There is no reason for
      // a Garmin password to stay in memory once it has been submitted.
      setPassword('');
    }
  }

  return (
    <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4">
      <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm dark:border-amber-800 dark:bg-amber-950/40">
        <p className="font-semibold">Než účet propojíš</p>
        <p className="mt-1">
          Garmin nenabízí oficiální API, takže se Sunder přihlašuje tvými údaji stejně jako web nebo
          mobilní aplikace. <strong>To je v rozporu s podmínkami Garminu.</strong> Reálné riziko je,
          že Garmin tvůj účet omezí nebo zablokuje za automatizovaný přístup.
        </p>
        <p className="mt-2">
          Heslo se ukládá zašifrované klíčem, který v databázi není. Odpojit účet můžeš kdykoliv —
          údaje se pak nenávratně smažou.
        </p>
      </div>

      <div>
        <label htmlFor="garmin-email" className="block text-sm font-medium">
          E-mail ke Garmin Connectu
        </label>
        <input
          id="garmin-email"
          type="email"
          autoComplete="off"
          required
          value={email}
          onChange={(e) => {
            setEmail(e.target.value);
          }}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
        />
      </div>

      <div>
        <label htmlFor="garmin-password" className="block text-sm font-medium">
          Heslo ke Garmin Connectu
        </label>
        <input
          id="garmin-password"
          type="password"
          // Keeping a third-party credential out of the browser's password
          // manager, where it would be offered on garmin.com itself.
          autoComplete="new-password"
          required
          value={password}
          onChange={(e) => {
            setPassword(e.target.value);
          }}
          className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 dark:border-slate-700 dark:bg-slate-900"
        />
      </div>

      <div className="flex items-start gap-2">
        <input
          id="accepted-risk"
          type="checkbox"
          checked={acceptedRisk}
          onChange={(e) => {
            setAcceptedRisk(e.target.checked);
          }}
          className="mt-1"
        />
        <label htmlFor="accepted-risk" className="text-sm">
          Rozumím tomu, že jde o neoficiální přístup v rozporu s podmínkami Garminu, a že můj Garmin
          účet může být kvůli tomu omezen.
        </label>
      </div>

      {message !== null && (
        <p
          role={status === 'error' ? 'alert' : 'status'}
          aria-live="polite"
          className={
            status === 'error'
              ? 'text-sm text-red-600 dark:text-red-400'
              : 'text-sm text-green-700 dark:text-green-400'
          }
        >
          {message}
        </p>
      )}

      <button
        type="submit"
        // The checkbox gates the button as well as the server. The Edge Function
        // refuses without it regardless; this just makes the requirement visible
        // rather than a surprise after submitting.
        disabled={status === 'submitting' || !acceptedRisk}
        className="bg-brand-600 hover:bg-brand-700 rounded-md px-4 py-2 font-medium text-white disabled:opacity-60"
      >
        {status === 'submitting' ? 'Propojuji…' : 'Propojit účet'}
      </button>
    </form>
  );
}
