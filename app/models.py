from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    document: Mapped[Document] = relationship(back_populates="versions")
    nodes: Mapped[list["Node"]] = relationship(back_populates="version", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    logical_key: Mapped[str] = mapped_column(String(500))
    heading: Mapped[str] = mapped_column(String(500))
    level: Mapped[int] = mapped_column(Integer)
    body_text: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("nodes.id"), nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    version: Mapped[DocumentVersion] = relationship(back_populates="nodes")
    parent: Mapped["Node | None"] = relationship(remote_side=[id], back_populates="children")
    children: Mapped[list["Node"]] = relationship(back_populates="parent", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint("version_id", "logical_key"),)


class NodeMatch(Base):
    __tablename__ = "node_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    to_node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    match_type: Mapped[str] = mapped_column(String(30))
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("from_node_id", "to_node_id"),)


class Selection(Base):
    __tablename__ = "selections"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    items: Mapped[list["SelectionItem"]] = relationship(
        back_populates="selection", cascade="all, delete-orphan"
    )


class SelectionItem(Base):
    __tablename__ = "selection_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"))
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"))
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id"))
    node_content_hash: Mapped[str] = mapped_column(String(64))
    selection: Mapped[Selection] = relationship(back_populates="items")


class Generation(Base):
    __tablename__ = "generations"

    id: Mapped[int] = mapped_column(primary_key=True)
    selection_id: Mapped[int] = mapped_column(ForeignKey("selections.id"))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), default="complete")
    output_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
