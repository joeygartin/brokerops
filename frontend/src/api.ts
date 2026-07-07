// Small bridge over the generated client's non-throwing result shape.

type SdkResult<T> = {
  data?: T;
  error?: unknown;
  response?: Response;
};

// A non-ok HTTP response, carrying the status so the Query layer can decide
// whether to retry (off for 4xx — a bad request won't fix itself). The message
// is unchanged (`api returned <status>`) so error UIs render identically.
export class ApiError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`api returned ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
}

// Unwrap `{ data, error, response }` into data-or-throw with the same error
// strings the views raised before the generated client (`api returned <status>`
// on a non-ok response; the original failure — e.g. a network TypeError — is
// rethrown untouched), so migrating to the client stayed behavior-neutral.
export function unwrap<T>(result: SdkResult<T>): T {
  if (result.error !== undefined || result.data === undefined) {
    if (result.response) throw new ApiError(result.response.status);
    throw result.error instanceof Error ? result.error : new Error(String(result.error));
  }
  return result.data;
}
