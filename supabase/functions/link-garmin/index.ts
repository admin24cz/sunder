/**
 * Edge Function: link a Garmin account (spec 6.5, ADR 0002).
 *
 * The only path by which a Garmin credential enters the system. It exists
 * because the frontend must never touch `garmin_connections` — that table has
 * no RLS policy and no grants for the anon key at all (spec 6.2).
 *
 * What this function can and cannot do is the point of ADR 0002. It holds
 * `CREDENTIAL_PUBLIC_KEY` and can therefore *seal* a password, but it has no
 * private key and cannot read back anything it has written. An attacker who
 * fully compromises this function and its configuration gains the ability to
 * overwrite credentials, not to read the ones already stored.
 *
 * Wire format, matching `sunder_sync.crypto.sealing` byte for byte:
 *
 *   version(1) = 2 || ephemeral X25519 public key(32) || ChaCha20-Poly1305 ct+tag
 *
 *   key   = BLAKE2s-256(x25519(ephemeral_private, server_public) || ephemeral_public)
 *   nonce = 12 zero bytes (safe: the key is unique per message)
 *   aad   = "sunder:garmin-password:sealed:v1:" || user_id
 */

import { createClient } from 'jsr:@supabase/supabase-js@2';
import { x25519 } from 'npm:@noble/curves@1.7.0/ed25519';
import { blake2s } from 'npm:@noble/hashes@1.6.1/blake2s';
import { chacha20poly1305 } from 'npm:@noble/ciphers@1.1.3/chacha';
import { randomBytes } from 'npm:@noble/hashes@1.6.1/utils';

const VERSION_SEALED_BOX = 2;
const AAD_PREFIX = 'sunder:garmin-password:sealed:v1:';

const CORS_HEADERS: Record<string, string> = {
  'Access-Control-Allow-Origin': Deno.env.get('ALLOWED_ORIGIN') ?? '*',
  // Every header supabase-js actually sends, not just the obvious two.
  //
  // `apikey` is the one that matters and the one that was missing: the client
  // sends it on every request, so the browser's preflight asks for it, and a
  // response that omits it makes the browser refuse to send the real request at
  // all. The failure looks like the function rejecting the call, but the call
  // never arrives — which is why invoking it directly from a script succeeded
  // while the form kept failing. Only browsers enforce CORS.
  //
  // `x-client-info` and `x-supabase-api-version` are sent by supabase-js too,
  // and omitting either would fail the same way the moment it starts sending
  // them.
  'Access-Control-Allow-Headers':
    'authorization, content-type, apikey, x-client-info, x-supabase-api-version',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  // Lets the browser reuse the preflight result instead of repeating it before
  // every attempt.
  'Access-Control-Max-Age': '86400',
};

interface LinkRequest {
  garmin_email?: unknown;
  garmin_password?: unknown;
  accepted_risk?: unknown;
}

function json(body: Record<string, unknown>, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, 'Content-Type': 'application/json' },
  });
}

/** Decode base64 into bytes, without pulling in another dependency. */
function fromBase64(value: string): Uint8Array {
  const binary = atob(value);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

/** Encode bytes as Postgres `bytea` hex input, which is what PostgREST wants. */
function toByteaHex(bytes: Uint8Array): string {
  let hex = '';
  for (const byte of bytes) hex += byte.toString(16).padStart(2, '0');
  return `\\x${hex}`;
}

/**
 * Seal a password so that only the holder of the private key can read it.
 *
 * The ephemeral private key exists only inside this function and is never
 * returned or stored, so this process cannot reverse its own output.
 */
function sealPassword(password: string, userId: string, serverPublicKey: Uint8Array): Uint8Array {
  const ephemeralPrivate = x25519.utils.randomPrivateKey();
  const ephemeralPublic = x25519.getPublicKey(ephemeralPrivate);

  const shared = x25519.getSharedSecret(ephemeralPrivate, serverPublicKey);

  // The ephemeral public key is mixed in alongside the shared secret so the
  // derived key is bound to this specific message.
  const key = blake2s(new Uint8Array([...shared, ...ephemeralPublic]), { dkLen: 32 });

  const aad = new TextEncoder().encode(AAD_PREFIX + userId);
  // A zero nonce is safe here and only here: the key is derived from a keypair
  // freshly generated above, so it is used for exactly one message.
  const nonce = new Uint8Array(12);
  const ciphertext = chacha20poly1305(key, nonce, aad).encrypt(
    new TextEncoder().encode(password),
  );

  const payload = new Uint8Array(1 + ephemeralPublic.length + ciphertext.length);
  payload[0] = VERSION_SEALED_BOX;
  payload.set(ephemeralPublic, 1);
  payload.set(ciphertext, 1 + ephemeralPublic.length);

  // Best-effort scrub. It does not undo any copy the runtime may have made, but
  // it costs nothing and removes the obvious one.
  ephemeralPrivate.fill(0);
  key.fill(0);

  return payload;
}

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: CORS_HEADERS });
  }
  if (request.method !== 'POST') {
    return json({ error: 'Metoda není podporována' }, 405);
  }

  const supabaseUrl = Deno.env.get('SUPABASE_URL');
  const serviceRoleKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  const publicKeyBase64 = Deno.env.get('CREDENTIAL_PUBLIC_KEY');

  if (!supabaseUrl || !serviceRoleKey || !publicKeyBase64) {
    // Names the missing variable in the server log, never in the response.
    console.error('Missing configuration:', {
      SUPABASE_URL: Boolean(supabaseUrl),
      SUPABASE_SERVICE_ROLE_KEY: Boolean(serviceRoleKey),
      CREDENTIAL_PUBLIC_KEY: Boolean(publicKeyBase64),
    });
    return json({ error: 'Služba není správně nakonfigurovaná' }, 500);
  }

  // Identify the caller from their own JWT. The user id comes from the verified
  // token and never from the request body — otherwise anyone could store a
  // credential against somebody else's account.
  const authHeader = request.headers.get('Authorization') ?? '';
  const anonKey = Deno.env.get('SUPABASE_ANON_KEY') ?? '';
  const userClient = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: authHeader } },
  });

  const { data: userData, error: userError } = await userClient.auth.getUser();
  if (userError || !userData.user) {
    return json({ error: 'Nejsi přihlášený' }, 401);
  }
  const userId = userData.user.id;

  let body: LinkRequest;
  try {
    body = (await request.json()) as LinkRequest;
  } catch {
    return json({ error: 'Neplatný požadavek' }, 400);
  }

  const email = typeof body.garmin_email === 'string' ? body.garmin_email.trim() : '';
  const password = typeof body.garmin_password === 'string' ? body.garmin_password : '';
  const acceptedRisk = body.accepted_risk === true;

  if (!email || !password) {
    return json({ error: 'Vyplň e-mail i heslo ke Garmin Connectu' }, 400);
  }
  // Spec 6.5: linking requires explicit, informed consent, because it puts the
  // user's Garmin account at risk of being blocked.
  if (!acceptedRisk) {
    return json({ error: 'Bez potvrzení rizika účet propojit nelze' }, 400);
  }

  let sealed: Uint8Array;
  try {
    sealed = sealPassword(password, userId, fromBase64(publicKeyBase64));
  } catch (error) {
    // The message is logged, the password is not — `error` here comes from key
    // decoding or the cipher, neither of which embeds the plaintext.
    console.error('Sealing failed:', error instanceof Error ? error.message : 'unknown');
    return json({ error: 'Heslo se nepodařilo bezpečně uložit' }, 500);
  }

  const adminClient = createClient(supabaseUrl, serviceRoleKey);
  const { error: writeError } = await adminClient.from('garmin_connections').upsert(
    {
      user_id: userId,
      garmin_email: email,
      garmin_password_encrypted: toByteaHex(sealed),
      // Re-linking after a failure has to clear the old state, or the sync
      // would keep skipping an account that now works (spec 7.3).
      status: 'active',
      last_error: null,
    },
    { onConflict: 'user_id' },
  );

  if (writeError) {
    // `writeError.message` can quote the row it failed on, which contains the
    // sealed credential. Only the code is logged.
    console.error('Failed to store connection, code:', writeError.code);
    return json({ error: 'Propojení se nepodařilo uložit' }, 500);
  }

  return json({ ok: true, garmin_email: email }, 200);
});
