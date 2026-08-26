"""Job handler mixins for :class:`RemoteWorker`.

Each module in this package groups the methods that process a specific job
type (mapping, generation, knowledge indexing, enrichment, content
collection, cleanup, kids). They are mixed into ``RemoteWorker`` via
multiple inheritance so the public class surface is unchanged.
"""
