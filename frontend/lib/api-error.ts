export class ApiError extends Error {
  constructor(message: string, public status = 0) { super(message); this.name = "ApiError"; }
}
