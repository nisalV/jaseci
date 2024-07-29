"""Core constructs for Jac Language."""

from dataclasses import asdict, dataclass, field, is_dataclass
from inspect import iscoroutine
from os import getenv
from re import IGNORECASE, compile
from typing import (
    Any,
    Callable,
    ClassVar,
    Iterable,
    Mapping,
    Type,
    TypeVar,
    cast,
)

from bson import ObjectId

from jaclang.compiler.constant import EdgeDir
from jaclang.runtimelib.architype import (
    Anchor as _Anchor,
    AnchorType,
    Architype as _Architype,
    DSFunc,
    Permission,
    populate_dataclasses,
)
from jaclang.runtimelib.utils import collect_node_connections

from motor.motor_asyncio import AsyncIOMotorClientSession

from orjson import dumps

from pymongo import ASCENDING, DeleteMany, DeleteOne, InsertOne, UpdateMany, UpdateOne
from pymongo.errors import ConnectionFailure, OperationFailure

from ..jaseci.datasources import Collection as BaseCollection
from ..jaseci.utils import logger


GENERIC_ID_REGEX = compile(r"^(g|n|e|w):([^:]*):([a-f\d]{24})$", IGNORECASE)
NODE_ID_REGEX = compile(r"^n:([^:]*):([a-f\d]{24})$", IGNORECASE)
EDGE_ID_REGEX = compile(r"^e:([^:]*):([a-f\d]{24})$", IGNORECASE)
WALKER_ID_REGEX = compile(r"^w:([^:]*):([a-f\d]{24})$", IGNORECASE)
TA = TypeVar("TA", bound="type[Architype]")


@dataclass
class BulkWrite:
    """Bulk Write builder."""

    SESSION_MAX_TRANSACTION_RETRY: ClassVar[int] = int(
        getenv("SESSION_TRANSACTION_MAX_RETRY") or "1"
    )
    SESSION_MAX_COMMIT_RETRY: ClassVar[int] = int(
        getenv("SESSION_MAX_COMMIT_RETRY") or "1"
    )

    operations: dict[
        AnchorType,
        list[InsertOne[Any] | DeleteMany | DeleteOne | UpdateMany | UpdateOne],
    ] = field(
        default_factory=lambda: {
            AnchorType.node: [],
            AnchorType.edge: [],
            AnchorType.walker: [],
            AnchorType.generic: [],  # ignored
        }
    )

    del_ops_nodes: list[ObjectId] = field(default_factory=list)
    del_ops_edges: list[ObjectId] = field(default_factory=list)
    del_ops_walker: list[ObjectId] = field(default_factory=list)

    def del_node(self, id: ObjectId) -> None:
        """Add node to delete many operations."""
        if not self.del_ops_nodes:
            self.operations[AnchorType.node].append(
                DeleteMany({"_id": {"$in": self.del_ops_nodes}})
            )

        self.del_ops_nodes.append(id)

    def del_edge(self, id: ObjectId) -> None:
        """Add edge to delete many operations."""
        if not self.del_ops_edges:
            self.operations[AnchorType.edge].append(
                DeleteMany({"_id": {"$in": self.del_ops_edges}})
            )

        self.del_ops_edges.append(id)

    def del_walker(self, id: ObjectId) -> None:
        """Add walker to delete many operations."""
        if not self.del_ops_walker:
            self.operations[AnchorType.walker].append(
                DeleteMany({"_id": {"$in": self.del_ops_walker}})
            )

        self.del_ops_walker.append(id)

    @property
    def has_operations(self) -> bool:
        """Check if has operations."""
        return any(val for val in self.operations.values())

    @staticmethod
    async def commit(session: AsyncIOMotorClientSession) -> None:
        """Commit current session."""
        commit_retry = 0
        commit_max_retry = BulkWrite.SESSION_MAX_COMMIT_RETRY
        while commit_retry <= commit_max_retry:
            try:
                await session.commit_transaction()
                break
            except (ConnectionFailure, OperationFailure) as ex:
                if ex.has_error_label("UnknownTransactionCommitResult"):
                    commit_retry += 1
                    logger.error(
                        "Error commiting bulk write! "
                        f"Retrying [{commit_retry}/{commit_max_retry}] ..."
                    )
                    continue
                logger.error(
                    f"Error commiting bulk write after max retry [{commit_max_retry}] !"
                )
                raise
            except Exception:
                await session.abort_transaction()
                logger.error("Error commiting bulk write!")
                raise

    async def execute(self, session: AsyncIOMotorClientSession) -> None:
        """Execute all operations."""
        transaction_retry = 0
        transaction_max_retry = self.SESSION_MAX_TRANSACTION_RETRY
        while transaction_retry <= transaction_max_retry:
            try:
                if node_operation := self.operations[AnchorType.node]:
                    await NodeAnchor.Collection.bulk_write(
                        node_operation, False, session
                    )
                if edge_operation := self.operations[AnchorType.edge]:
                    await EdgeAnchor.Collection.bulk_write(
                        edge_operation, False, session
                    )
                if walker_operation := self.operations[AnchorType.walker]:
                    await WalkerAnchor.Collection.bulk_write(
                        walker_operation, False, session
                    )
                await self.commit(session)
                break
            except (ConnectionFailure, OperationFailure) as ex:
                if ex.has_error_label("TransientTransactionError"):
                    transaction_retry += 1
                    logger.error(
                        "Error executing bulk write! "
                        f"Retrying [{transaction_retry}/{transaction_max_retry}] ..."
                    )
                    continue
                logger.error(
                    f"Error executing bulk write after max retry [{transaction_max_retry}] !"
                )
                raise
            except Exception:
                logger.error("Error executing bulk write!")
                raise


@dataclass(eq=False)
class Anchor(_Anchor):
    """Object Anchor."""

    id: ObjectId = field(default_factory=ObjectId)  # type: ignore[assignment]
    root: ObjectId | None = None  # type: ignore[assignment]
    architype: "Architype | None" = None
    connected: bool = False

    # checker if needs to update on db
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    # context checker if update happens for each field
    hashes: dict[str, int] = field(default_factory=dict)

    class Collection(BaseCollection["Anchor"]):
        """Anchor collection interface."""

        pass

    @staticmethod
    def ref(ref_id: str) -> "Anchor | None":
        """Return ObjectAnchor instance if ."""
        if matched := GENERIC_ID_REGEX.search(ref_id):
            cls: type = Anchor
            match AnchorType(matched.group(1)):
                case AnchorType.node:
                    cls = NodeAnchor
                case AnchorType.edge:
                    cls = EdgeAnchor
                case AnchorType.walker:
                    cls = WalkerAnchor
                case _:
                    pass
            return cls(name=matched.group(2), id=ObjectId(matched.group(3)))
        return None

    ####################################################
    #                 QUERY OPERATIONS                 #
    ####################################################

    @property
    def _set(self) -> dict:
        if "$set" not in self.changes:
            self.changes["$set"] = {}
        return self.changes["$set"]

    @property
    def _unset(self) -> dict:
        if "$unset" not in self.changes:
            self.changes["$unset"] = {}
        return self.changes["$unset"]

    @property
    def _add_to_set(self) -> dict:
        if "$addToSet" not in self.changes:
            self.changes["$addToSet"] = {}

        return self.changes["$addToSet"]

    @property
    def _pull(self) -> dict:
        if "$pull" not in self.changes:
            self.changes["$pull"] = {}

        return self.changes["$pull"]

    def add_to_set(self, field: str, anchor: "Anchor", remove: bool = False) -> None:
        """Add to set."""
        if field not in (add_to_set := self._add_to_set):
            add_to_set[field] = {"$each": set()}

        ops: set = add_to_set[field]["$each"]

        if remove:
            if anchor in ops:
                ops.remove(anchor)
        else:
            ops.add(anchor)
            self.pull(field, anchor, True)

    def pull(self, field: str, anchor: "Anchor", remove: bool = False) -> None:
        """Pull from set."""
        if field not in (pull := self._pull):
            pull[field] = {"$in": set()}

        ops: set = pull[field]["$in"]

        if remove:
            if anchor in ops:
                ops.remove(anchor)
        else:
            ops.add(anchor)
            self.add_to_set(field, anchor, True)

    def connect_edge(self, anchor: "Anchor") -> None:
        """Push update that there's newly added edge."""
        self.add_to_set("edges", anchor)

    def disconnect_edge(self, anchor: "Anchor") -> None:
        """Push update that there's edge that has been removed."""
        self.pull("edges", anchor)

    # def whitelist_nodes(self, whitelist: bool = True) -> None:
    #     """Toggle node whitelist/blacklist."""
    #     if whitelist != self.access.nodes.whitelist:
    #         self._set.update({"access.nodes.whitelist": whitelist})

    # def allow_node(self, node: Anchor, level: int = 0) -> None:
    #     """Allow all access from target node to current Architype."""
    #     access = self.access.nodes
    #     if access.whitelist:
    #         if level != access.anchors.get(node, -1):
    #             access.anchors[node] = level
    #             self._set.update({f"access.nodes.{node.ref_id}": level})
    #             self._unset.pop(f"access.nodes.{node.ref_id}", None)
    #     else:
    #         self.disallow_node(node, level)

    # def disallow_node(self, node: Anchor, level: int = 0) -> None:
    #     """Disallow all access from target node to current Architype."""
    #     access = self.access.nodes
    #     if access.whitelist:
    #         if access.anchors.pop(node, None) is not None:
    #             self._unset.update({f"access.nodes.{node.ref_id}": True})
    #             self._set.pop(f"access.nodes.{node.ref_id}", None)
    #     else:
    #         self.allow_node(node, level)

    # def add_types(self, type: type[NodeArchitype]) -> None:
    #     """Add type checking."""
    #     if not self.access.types.get(type):
    #         name = type.__ref_cls__()
    #         self._set.update({f"access.types.{name}": {}})
    #         self._unset.pop(f"access.types.{name}", None)

    # def remove_types(self, type: type[NodeArchitype]) -> None:
    #     """Remove type checking."""
    #     if self.access.types.pop(type, None):
    #         name = type.__ref_cls__()
    #         self._unset.update({f"access.types.{name}": True})
    #         self._set.pop(f"access.types.{name}", None)

    # def whitelist_types(
    #     self, type: type[NodeArchitype], whitelist: bool = True
    # ) -> None:
    #     """Toggle type whitelist/blacklist."""
    #     if (access := self.access.types.get(type)) and whitelist != access.whitelist:
    #         self._set.update(
    #             {f"access.types.{type.__ref_cls__()}.whitelist": whitelist}
    #         )

    # def allow_type(
    #     self, type: type[NodeArchitype], node: Anchor, level: int = 0
    # ) -> None:
    #     """Allow all access from target type graph to current Architype."""
    #     if access := self.access.types.get(type):
    #         if access.whitelist:
    #             if level != access.anchors.get(node, -1):
    #                 access.anchors[node] = level
    #                 name = type.__ref_cls__()
    #                 self._set.update({f"access.types.{name}.{node.ref_id}": level})
    #                 self._unset.pop(f"access.types.{name}.{node.ref_id}", None)
    #         else:
    #             self.disallow_type(type, node, level)

    # def disallow_type(
    #     self, type: type[NodeArchitype], node: Anchor, level: int = 0
    # ) -> None:
    #     """Disallow all access from target type graph to current Architype."""
    #     if access := self.access.types.get(type):
    #         if access.whitelist:
    #             if access.anchors.pop(node, None) is not None:
    #                 name = type.__ref_cls__()
    #                 self._unset.update({f"access.types.{name}.{node.ref_id}": True})
    #                 self._set.pop(f"access.types.{name}.{node.ref_id}", None)
    #         else:
    #             self.allow_type(type, node, level)

    def whitelist_roots(self, whitelist: bool = True) -> None:
        """Toggle root whitelist/blacklist."""
        if whitelist != self.access.roots.whitelist:
            self._set.update({"access.roots.whitelist": whitelist})

    def allow_root(self, root: "Anchor", level: int = 0) -> None:
        """Allow all access from target root graph to current Architype."""
        access = self.access.roots
        if access.whitelist:
            if level != access.anchors.get(root, -1):
                access.anchors[root] = level
                self._set.update({f"access.roots.{root.ref_id}": level})
                self._unset.pop(f"access.roots.{root.ref_id}", None)
        else:
            self.disallow_root(root, level)

    def disallow_root(self, root: "Anchor", level: int = 0) -> None:
        """Disallow all access from target root graph to current Architype."""
        access = self.access.roots
        if access.whitelist:
            if access.anchors.pop(root, None) is not None:
                self._unset.update({f"access.roots.{root.ref_id}": True})
                self._set.pop(f"access.roots.{root.ref_id}", None)
        else:
            self.allow_root(root, level)

    def unrestrict(self, level: int = 0) -> None:
        """Allow everyone to access current Architype."""
        if level != self.access.all:
            self.access.all = level
            self._set.update({"access.all": level})

    def restrict(self) -> None:
        """Disallow others to access current Architype."""
        if self.access.all > -1:
            self.access.all = -1
            self._set.update({"access.all": -1})

    # ------------------------------------------------ #

    async def sync(self, node: "NodeAnchor | None" = None) -> "Architype | None":
        """Retrieve the Architype from db and return."""
        if architype := self.architype:
            if (node or self).has_read_access(self):
                return architype
            return None

        from .context import JaseciContext

        jsrc = JaseciContext.get().datasource
        anchor = await jsrc.find_one(self.type, self.id)

        if anchor and (node or self).has_read_access(anchor):
            self.__dict__.update(anchor.__dict__)

        return self.architype

    def allocate(self) -> None:
        """Allocate hashes and memory."""
        from .context import JASECI_CONTEXT

        if jctx := JASECI_CONTEXT.get(None):
            if jctx.root:
                self.root = jctx.root.id
            jctx.datasource.set(self)

    async def _save(
        self,
        bulk_write: BulkWrite,
    ) -> None:
        """Save Anchor."""
        if self.architype:
            if not self.connected:
                self.connected = True
                self.sync_hash()
                await self.insert(bulk_write)
            elif self.current_access_level > 0:
                await self.update(bulk_write, True)

    async def save(self, session: AsyncIOMotorClientSession | None = None) -> BulkWrite:
        """Save Anchor."""
        bulk_write = BulkWrite()

        await self._save(bulk_write)

        if bulk_write.has_operations:
            if session:
                await bulk_write.execute(session)
            else:
                async with await BaseCollection.get_session() as session:
                    async with session.start_transaction():
                        await bulk_write.execute(session)

        return bulk_write

    async def insert(
        self,
        bulk_write: BulkWrite,
    ) -> None:
        """Insert Anchor."""
        bulk_write.operations[self.type].append(InsertOne(self.serialize()))

    async def update(self, bulk_write: BulkWrite, propagate: bool = False) -> None:
        """Update Anchor."""
        changes = self.changes
        self.changes = {}  # renew reference

        operations = bulk_write.operations[self.type]
        operation_filter = {"_id": self.id}

        ############################################################
        #                     POPULATE CONTEXT                     #
        ############################################################

        if self.current_access_level > 1:
            set_architype = changes.pop("$set", {})
            if is_dataclass(architype := self.architype) and not isinstance(
                architype, type
            ):
                for key, val in asdict(architype).items():
                    if (h := hash(dumps(val))) != self.hashes.get(key):
                        self.hashes[key] = h
                        set_architype[f"architype.{key}"] = val
            if set_architype:
                changes["$set"] = set_architype
        else:
            changes.pop("$set", None)
            changes.pop("$unset", None)

        # -------------------------------------------------------- #

        if self.type is AnchorType.node:
            ############################################################
            #                   POPULATE ADDED EDGES                   #
            ############################################################
            added_edges: set[Anchor] = (
                changes.get("$addToSet", {}).get("edges", {}).get("$each", [])
            )
            if added_edges:
                _added_edges = []
                for anchor in added_edges:
                    if propagate:
                        await anchor._save(bulk_write)
                    _added_edges.append(anchor.ref_id)
                changes["$addToSet"]["edges"]["$each"] = _added_edges
            else:
                changes.pop("$addToSet", None)

            # -------------------------------------------------------- #

            ############################################################
            #                  POPULATE REMOVED EDGES                  #
            ############################################################
            pulled_edges: set[Anchor] = (
                changes.get("$pull", {}).get("edges", {}).get("$in", [])
            )
            if pulled_edges:
                _pulled_edges = []
                for anchor in pulled_edges:
                    if propagate:
                        bulk_write.del_edge(anchor.id)
                    _pulled_edges.append(anchor.ref_id)

                if added_edges:
                    # Isolate pull to avoid conflict with addToSet
                    changes.pop("$pull", None)
                    operations.append(
                        UpdateOne(
                            operation_filter,
                            {"$pull": {"edges": {"$in": _pulled_edges}}},
                        )
                    )
                else:
                    changes["$pull"]["edges"]["$in"] = _pulled_edges
            else:
                changes.pop("$pull", None)

            # -------------------------------------------------------- #

        if changes:
            operations.append(UpdateOne(operation_filter, changes))

    async def destroy(self) -> None:
        """Save Anchor."""
        raise NotImplementedError("destroy must be implemented in subclasses")

    def data_hash(self) -> int:
        """Get current serialization hash."""
        return hash(dumps(self.serialize(True)))

    def sync_hash(self) -> None:
        """Sync current serialization hash."""
        if architype := self.architype:
            architype

        self.hash = self.data_hash()

    def serialize(self, dumps: bool = False) -> dict[str, object]:
        """Serialize Anchor."""
        return {
            "_id": str(self.id) if dumps else self.id,
            "name": self.name,
            "root": str(self.root) if dumps else self.root,
            "access": self.access.serialize(),
            "architype": (
                asdict(self.architype)
                if is_dataclass(self.architype) and not isinstance(self.architype, type)
                else {}
            ),
        }


@dataclass(eq=False)
class NodeAnchor(Anchor):
    """Node Anchor."""

    type: ClassVar[AnchorType] = AnchorType.node
    architype: "NodeArchitype | None" = None
    edges: list["EdgeAnchor"] = field(default_factory=list)

    class Collection(BaseCollection["NodeAnchor"]):
        """NodeAnchor collection interface."""

        __collection__: str | None = "node"
        __default_indexes__: list[dict] = [
            {"keys": [("_id", ASCENDING), ("name", ASCENDING), ("root", ASCENDING)]}
        ]

        @classmethod
        def __document__(cls, doc: Mapping[str, Any]) -> "NodeAnchor":
            """Parse document to NodeAnchor."""
            doc = cast(dict, doc)
            architype: dict = doc.pop("architype")
            anchor = NodeAnchor(
                id=doc.pop("_id"),
                edges=[e for edge in doc.pop("edges") if (e := EdgeAnchor.ref(edge))],
                access=Permission.deserialize(doc.pop("access")),
                connected=True,
                hashes={key: hash(dumps(val)) for key, val in architype.items()},
                **doc,
            )
            architype_cls = NodeArchitype.__get_class__(doc.get("name") or "Root")
            anchor.architype = architype_cls(
                __jac__=anchor, **populate_dataclasses(architype_cls, architype)
            )
            anchor.sync_hash()
            return anchor

    @classmethod
    def ref(cls, ref_id: str) -> "NodeAnchor | None":
        """Return NodeAnchor instance if existing."""
        if match := NODE_ID_REGEX.search(ref_id):
            return cls(
                name=match.group(1),
                id=ObjectId(match.group(2)),
            )
        return None

    async def insert(
        self,
        bulk_write: BulkWrite,
    ) -> None:
        """Insert Anchor."""
        for edge in self.edges:
            await edge._save(bulk_write)

        await super().insert(bulk_write)

    async def destroy(self) -> None:
        """Delete Anchor."""
        if self.architype and self.current_access_level > 1:
            from .context import JaseciContext

            jsrc = JaseciContext.get().datasource
            for edge in self.edges:
                await edge.destroy()

            jsrc.remove(self)

    async def sync(self, node: "NodeAnchor | None" = None) -> "NodeArchitype | None":
        """Retrieve the Architype from db and return."""
        return cast(NodeArchitype | None, await super().sync(node))

    def connect_node(self, nd: "NodeAnchor", edg: "EdgeAnchor") -> None:
        """Connect a node with given edge."""
        edg.attach(self, nd)

    async def get_edges(
        self,
        dir: EdgeDir,
        filter_func: Callable[[list["EdgeArchitype"]], list["EdgeArchitype"]] | None,
        target_cls: list[Type["NodeArchitype"]] | None,
    ) -> list["EdgeArchitype"]:
        """Get edges connected to this node."""
        ret_edges: list[EdgeArchitype] = []
        for anchor in self.edges:
            if (
                (architype := await anchor.sync(self))
                and (source := anchor.source)
                and (target := anchor.target)
                and (not filter_func or filter_func([architype]))
            ):
                src_arch = await source.sync()
                trg_arch = await target.sync()

                if (
                    dir in [EdgeDir.OUT, EdgeDir.ANY]
                    and self == source
                    and trg_arch
                    and (not target_cls or trg_arch.__class__ in target_cls)
                    and source.has_read_access(target)
                ):
                    ret_edges.append(architype)
                if (
                    dir in [EdgeDir.IN, EdgeDir.ANY]
                    and self == target
                    and src_arch
                    and (not target_cls or src_arch.__class__ in target_cls)
                    and target.has_read_access(source)
                ):
                    ret_edges.append(architype)
        return ret_edges

    async def edges_to_nodes(
        self,
        dir: EdgeDir,
        filter_func: Callable[[list["EdgeArchitype"]], list["EdgeArchitype"]] | None,
        target_cls: list[Type["NodeArchitype"]] | None,
    ) -> list["NodeArchitype"]:
        """Get set of nodes connected to this node."""
        ret_edges: list[NodeArchitype] = []
        for anchor in self.edges:
            if (
                (architype := await anchor.sync(self))
                and (source := anchor.source)
                and (target := anchor.target)
                and (not filter_func or filter_func([architype]))
            ):
                src_arch = await source.sync()
                trg_arch = await target.sync()

                if (
                    dir in [EdgeDir.OUT, EdgeDir.ANY]
                    and self == source
                    and trg_arch
                    and (not target_cls or trg_arch.__class__ in target_cls)
                    and source.has_read_access(target)
                ):
                    ret_edges.append(trg_arch)
                if (
                    dir in [EdgeDir.IN, EdgeDir.ANY]
                    and self == target
                    and src_arch
                    and (not target_cls or src_arch.__class__ in target_cls)
                    and target.has_read_access(source)
                ):
                    ret_edges.append(src_arch)
        return ret_edges

    def remove_edge(self, edge: "EdgeAnchor") -> None:
        """Remove reference without checking sync status."""
        for idx, ed in enumerate(self.edges):
            if ed.id == edge.id:
                self.edges.pop(idx)
                break

    def gen_dot(self, dot_file: str | None = None) -> str:
        """Generate Dot file for visualizing nodes and edges."""
        visited_nodes: set[NodeAnchor] = set()
        connections: set[tuple[NodeArchitype, NodeArchitype, str]] = set()
        unique_node_id_dict = {}

        collect_node_connections(self, visited_nodes, connections)  # type: ignore[arg-type]
        dot_content = 'digraph {\nnode [style="filled", shape="ellipse", fillcolor="invis", fontcolor="black"];\n'
        for idx, i in enumerate([nodes_.architype for nodes_ in visited_nodes]):
            unique_node_id_dict[i] = (i.__class__.__name__, str(idx))
            dot_content += f'{idx} [label="{i}"];\n'
        dot_content += 'edge [color="gray", style="solid"];\n'

        for pair in list(set(connections)):
            dot_content += (
                f"{unique_node_id_dict[pair[0]][1]} -> {unique_node_id_dict[pair[1]][1]}"
                f' [label="{pair[2]}"];\n'
            )
        if dot_file:
            with open(dot_file, "w") as f:
                f.write(dot_content + "}")
        return dot_content + "}"

    async def spawn_call(self, walk: "WalkerAnchor") -> "WalkerArchitype":
        """Invoke data spatial call."""
        return await walk.spawn_call(self)

    def serialize(self, dumps: bool = False) -> dict[str, object]:
        """Serialize Node Anchor."""
        return {
            **super().serialize(dumps),
            "edges": [edge.ref_id for edge in self.edges],
        }


@dataclass(eq=False)
class EdgeAnchor(Anchor):
    """Edge Anchor."""

    type: ClassVar[AnchorType] = AnchorType.edge
    architype: "EdgeArchitype | None" = None
    source: NodeAnchor | None = None
    target: NodeAnchor | None = None
    is_undirected: bool = False

    class Collection(BaseCollection["EdgeAnchor"]):
        """EdgeAnchor collection interface."""

        __collection__: str | None = "edge"
        __default_indexes__: list[dict] = [
            {"keys": [("_id", ASCENDING), ("name", ASCENDING), ("root", ASCENDING)]}
        ]

        @classmethod
        def __document__(cls, doc: Mapping[str, Any]) -> "EdgeAnchor":
            """Parse document to EdgeAnchor."""
            doc = cast(dict, doc)
            architype: dict = doc.pop("architype")
            anchor = EdgeAnchor(
                id=doc.pop("_id"),
                source=NodeAnchor.ref(doc.pop("source")),
                target=NodeAnchor.ref(doc.pop("target")),
                access=Permission.deserialize(doc.pop("access")),
                connected=True,
                hashes={key: hash(dumps(val)) for key, val in architype.items()},
                **doc,
            )
            architype_cls = EdgeArchitype.__get_class__(
                doc.get("name") or "GenericEdge"
            )
            anchor.architype = architype_cls(
                __jac__=anchor, **populate_dataclasses(architype_cls, architype)
            )
            anchor.sync_hash()
            return anchor

    @classmethod
    def ref(cls, ref_id: str) -> "EdgeAnchor | None":
        """Return EdgeAnchor instance if existing."""
        if match := EDGE_ID_REGEX.search(ref_id):
            return cls(
                name=match.group(1),
                id=ObjectId(match.group(2)),
            )
        return None

    async def insert(self, bulk_write: BulkWrite) -> None:
        """Insert Anchor."""
        if source := self.source:
            await source._save(bulk_write)

        if target := self.target:
            await target._save(bulk_write)

        await super().insert(bulk_write)

    async def destroy(self) -> None:
        """Delete Anchor."""
        if self.architype and self.current_access_level == 1:
            from .context import JaseciContext

            jsrc = JaseciContext.get().datasource

            self.detach()
            jsrc.remove(self)

    async def sync(self, node: "NodeAnchor | None" = None) -> "EdgeArchitype | None":
        """Retrieve the Architype from db and return."""
        return cast(EdgeArchitype | None, await super().sync(node))

    def attach(
        self, src: NodeAnchor, trg: NodeAnchor, is_undirected: bool = False
    ) -> "EdgeAnchor":
        """Attach edge to nodes."""
        self.source = src
        self.target = trg
        self.is_undirected = is_undirected
        src.edges.append(self)
        trg.edges.append(self)
        src.connect_edge(self)
        trg.connect_edge(self)
        return self

    def detach(self) -> None:
        """Detach edge from nodes."""
        if source := self.source:
            source.remove_edge(self)
            source.disconnect_edge(self)
        if target := self.target:
            target.remove_edge(self)
            target.disconnect_edge(self)

        self.source = None
        self.target = None

    async def spawn_call(self, walk: "WalkerAnchor") -> "WalkerArchitype":
        """Invoke data spatial call."""
        if target := self.target:
            return await walk.spawn_call(target)
        else:
            raise ValueError("Edge has no target.")

    def serialize(self, dumps: bool = False) -> dict[str, object]:
        """Serialize Node Anchor."""
        return {
            **super().serialize(dumps),
            "source": self.source.ref_id if self.source else None,
            "target": self.target.ref_id if self.target else None,
        }


@dataclass(eq=False)
class WalkerAnchor(Anchor):
    """Walker Anchor."""

    type: ClassVar[AnchorType] = AnchorType.walker
    architype: "WalkerArchitype | None" = None
    path: list[Anchor] = field(default_factory=list)
    next: list[Anchor] = field(default_factory=list)
    returns: list[Any] = field(default_factory=list)
    ignores: list[Anchor] = field(default_factory=list)
    disengaged: bool = False
    persistent: bool = False  # Disabled initially but can be adjusted

    class Collection(BaseCollection["WalkerAnchor"]):
        """WalkerAnchor collection interface."""

        __collection__: str | None = "walker"
        __default_indexes__: list[dict] = [
            {"keys": [("_id", ASCENDING), ("name", ASCENDING), ("root", ASCENDING)]}
        ]

        @classmethod
        def __document__(cls, doc: Mapping[str, Any]) -> "WalkerAnchor":
            """Parse document to WalkerAnchor."""
            doc = cast(dict, doc)
            architype: dict = doc.pop("architype")
            anchor = WalkerAnchor(
                id=doc.pop("_id"),
                access=Permission.deserialize(doc.pop("access")),
                connected=True,
                hashes={key: hash(dumps(val)) for key, val in architype.items()},
                **doc,
            )
            architype_cls = WalkerArchitype.__get_class__(doc.get("name") or "")
            anchor.architype = architype_cls(
                __jac__=anchor, **populate_dataclasses(architype_cls, architype)
            )
            anchor.sync_hash()
            return anchor

    @classmethod
    def ref(cls, ref_id: str) -> "WalkerAnchor | None":
        """Return EdgeAnchor instance if existing."""
        if ref_id and (match := WALKER_ID_REGEX.search(ref_id)):
            return cls(
                name=match.group(1),
                id=ObjectId(match.group(2)),
            )
        return None

    async def destroy(self) -> None:
        """Delete Anchor."""
        if self.architype and self.current_access_level > 1:
            from .context import JaseciContext

            JaseciContext.get().datasource.remove(self)

    async def sync(self, node: "NodeAnchor | None" = None) -> "WalkerArchitype | None":
        """Retrieve the Architype from db and return."""
        return cast(WalkerArchitype | None, await super().sync(node))

    async def visit_node(self, anchors: Iterable[NodeAnchor | EdgeAnchor]) -> bool:
        """Walker visits node."""
        before_len = len(self.next)
        for anchor in anchors:
            if anchor not in self.ignores:
                if isinstance(anchor, NodeAnchor):
                    self.next.append(anchor)
                elif isinstance(anchor, EdgeAnchor):
                    if await anchor.sync() and (target := anchor.target):
                        self.next.append(target)
                    else:
                        raise ValueError("Edge has no target.")
        return len(self.next) > before_len

    async def ignore_node(self, anchors: Iterable[NodeAnchor | EdgeAnchor]) -> bool:
        """Walker ignores node."""
        before_len = len(self.ignores)
        for anchor in anchors:
            if anchor not in self.ignores:
                if isinstance(anchor, NodeAnchor):
                    self.ignores.append(anchor)
                elif isinstance(anchor, EdgeAnchor):
                    if await anchor.sync() and (target := anchor.target):
                        self.ignores.append(target)
                    else:
                        raise ValueError("Edge has no target.")
        return len(self.ignores) > before_len

    def disengage_now(self) -> None:
        """Disengage walker from traversal."""
        self.disengaged = True

    async def await_if_coroutine(self, ret: Any) -> Any:  # noqa: ANN401
        """Await return if it's a coroutine."""
        if iscoroutine(ret):
            ret = await ret

        self.returns.append(ret)

        return ret

    async def spawn_call(self, nd: Anchor) -> "WalkerArchitype":
        """Invoke data spatial call."""
        if walker := await self.sync():
            self.path = []
            self.next = [nd]
            self.returns = []
            while len(self.next):
                if node := await self.next.pop(0).sync():
                    for i in node._jac_entry_funcs_:
                        if not i.trigger or isinstance(walker, i.trigger):
                            if i.func:
                                await self.await_if_coroutine(i.func(node, walker))
                            else:
                                raise ValueError(f"No function {i.name} to call.")
                        if self.disengaged:
                            return walker
                    for i in walker._jac_entry_funcs_:
                        if not i.trigger or isinstance(node, i.trigger):
                            if i.func:
                                await self.await_if_coroutine(i.func(walker, node))
                            else:
                                raise ValueError(f"No function {i.name} to call.")
                        if self.disengaged:
                            return walker
                    for i in walker._jac_exit_funcs_:
                        if not i.trigger or isinstance(node, i.trigger):
                            if i.func:
                                await self.await_if_coroutine(i.func(walker, node))
                            else:
                                raise ValueError(f"No function {i.name} to call.")
                        if self.disengaged:
                            return walker
                    for i in node._jac_exit_funcs_:
                        if not i.trigger or isinstance(walker, i.trigger):
                            if i.func:
                                await self.await_if_coroutine(i.func(node, walker))
                            else:
                                raise ValueError(f"No function {i.name} to call.")
                        if self.disengaged:
                            return walker
            self.ignores = []
            return walker
        raise Exception(f"Invalid Reference {self.ref_id}")


class Architype(_Architype):
    """Architype Protocol."""

    __jac__: Anchor

    def __init__(self, __jac__: Anchor | None = None) -> None:
        """Create default architype."""
        self.__jac__ = __jac__ or Anchor(architype=self)
        self.__jac__.allocate()

    @classmethod
    def __ref_cls__(cls) -> str:
        """Get class naming."""
        return f"g:{cls.__name__}"


class NodeArchitype(Architype):
    """Node Architype Protocol."""

    __jac__: NodeAnchor

    def __init__(self, __jac__: NodeAnchor | None = None) -> None:
        """Create node architype."""
        self.__jac__ = __jac__ or NodeAnchor(
            name=self.__class__.__name__, architype=self
        )
        self.__jac__.allocate()

    @classmethod
    def __ref_cls__(cls) -> str:
        """Get class naming."""
        return f"n:{cls.__name__}"


class EdgeArchitype(Architype):
    """Edge Architype Protocol."""

    __jac__: EdgeAnchor

    def __init__(self, __jac__: EdgeAnchor | None = None) -> None:
        """Create edge architype."""
        self.__jac__ = __jac__ or EdgeAnchor(
            name=self.__class__.__name__, architype=self
        )
        self.__jac__.allocate()

    @classmethod
    def __ref_cls__(cls) -> str:
        """Get class naming."""
        return f"e:{cls.__name__}"


class WalkerArchitype(Architype):
    """Walker Architype Protocol."""

    __jac__: WalkerAnchor

    def __init__(self, __jac__: WalkerAnchor | None = None) -> None:
        """Create walker architype."""
        self.__jac__ = __jac__ or WalkerAnchor(
            name=self.__class__.__name__, architype=self
        )
        self.__jac__.allocate()

    @classmethod
    def __ref_cls__(cls) -> str:
        """Get class naming."""
        return f"w:{cls.__name__}"


@dataclass(eq=False)
class GenericEdge(EdgeArchitype):
    """Generic Root Node."""

    _jac_entry_funcs_: ClassVar[list[DSFunc]] = []  # type: ignore[misc]
    _jac_exit_funcs_: ClassVar[list[DSFunc]] = []  # type: ignore[misc]

    def __init__(self, __jac__: EdgeAnchor | None = None) -> None:
        """Create walker architype."""
        self.__jac__ = __jac__ or EdgeAnchor(architype=self)
        self.__jac__.allocate()


@dataclass(eq=False)
class Root(NodeArchitype):
    """Generic Root Node."""

    _jac_entry_funcs_: ClassVar[list[DSFunc]] = []  # type: ignore[misc]
    _jac_exit_funcs_: ClassVar[list[DSFunc]] = []  # type: ignore[misc]

    def __init__(self, __jac__: NodeAnchor | None = None) -> None:
        """Create walker architype."""
        self.__jac__ = __jac__ or NodeAnchor(architype=self)
        self.__jac__.allocate()
