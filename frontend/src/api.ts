// Small bridge over the generated client's non-throwing result shape.

type SdkResult<T> = {
  data?: T;
  error?: unknown;
  response?: Response;
};

// Unwrap `{ data, error, response }` into data-or-throw with the same error
// strings the views raised before the generated client (`api returned <status>`
// on a non-ok response; the original failure — e.g. a network TypeError — is
// rethrown untouched), so migrating to the client stayed behavior-neutral.
export function unwrap<T>(result: SdkResult<T>): T {
  if (result.error !== undefined || result.data === undefined) {
    if (result.response) throw new Error(`api returned ${result.response.status}`);
    throw result.error instanceof Error ? result.error : new Error(String(result.error));
  }
  return result.data;
}
