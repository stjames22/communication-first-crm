declare module "pg" {
  export interface QueryResultRow {
    [column: string]: any;
  }

  export interface QueryResult<T extends QueryResultRow = QueryResultRow> {
    rows: T[];
    rowCount: number | null;
  }

  export class Pool {
    constructor(config?: { connectionString?: string });
    query<T extends QueryResultRow = QueryResultRow>(text: string, params?: unknown[]): Promise<QueryResult<T>>;
    connect(): Promise<PoolClient>;
  }

  export interface PoolClient {
    query<T extends QueryResultRow = QueryResultRow>(text: string, params?: unknown[]): Promise<QueryResult<T>>;
    release(): void;
  }
}
