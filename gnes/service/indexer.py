#  Tencent is pleased to support the open source community by making GNES available.
#
#  Copyright (C) 2019 THL A29 Limited, a Tencent company. All rights reserved.
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import numpy as np

from .base import BaseService as BS, MessageHandler, ServiceError
from ..proto import gnes_pb2, blob2array


class IndexerService(BS):
    handler = MessageHandler(BS.handler)

    def post_init(self):
        from ..indexer.base import BaseIndexer
        # print('id: %s, before: %r' % (threading.get_ident(), self._model))
        self._model = self.load_model(BaseIndexer)
        # self._tmp_a = threading.get_ident()
        # print('id: %s, after: %r, self._tmp_a: %r' % (threading.get_ident(), self._model, self._tmp_a))

    @handler.register(gnes_pb2.Request.IndexRequest)
    def _handler_index(self, msg: 'gnes_pb2.Message'):
        # print('tid: %s, model: %r, self._tmp_a: %r' % (threading.get_ident(), self._model, self._tmp_a))
        # if self._tmp_a != threading.get_ident():
        #     print('tid: %s, tmp_a: %r !!! %r' % (threading.get_ident(), self._tmp_a, self._handler_index))
        from ..indexer.base import BaseChunkIndexer, BaseDocIndexer
        if isinstance(self._model, BaseChunkIndexer):
            self._handler_chunk_index(msg)
        elif isinstance(self._model, BaseDocIndexer):
            self._handler_doc_index(msg)
        else:
            raise ServiceError(
                'unsupported indexer, dont know how to use %s to handle this message' % self._model.__bases__)
        msg.response.index.status = gnes_pb2.Response.SUCCESS
        self.is_model_changed.set()

    def _handler_chunk_index(self, msg: 'gnes_pb2.Message'):
        embed_info = []

        for d in msg.request.index.docs:
            if not d.chunks:
                self.logger.warning('document (doc_id=%s) contains no chunks!' % d.doc_id)
                continue

            embed_info += [(blob2array(c.embedding), d.doc_id, c.offset, c.weight) for c in d.chunks if
                           c.embedding.data]

        if embed_info:
            vecs, doc_ids, offsets, weights = zip(*embed_info)
            self._model.add(list(zip(doc_ids, offsets)), np.stack(vecs), weights)
        else:
            self.logger.warning('chunks contain no embedded vectors, the indexer will do nothing')

    def _handler_doc_index(self, msg: 'gnes_pb2.Message'):
        self._model.add([d.doc_id for d in msg.request.index.docs],
                        [d for d in msg.request.index.docs],
                        [d.weight for d in msg.request.index.docs])

    def _put_result_into_message(self, results, msg: 'gnes_pb2.Message'):
        msg.response.search.ClearField('topk_results')
        msg.response.search.topk_results.extend(results)
        msg.response.search.top_k = len(results)
        msg.response.search.is_big_score_similar = self._model.is_big_score_similar

    @handler.register(gnes_pb2.Request.QueryRequest)
    def _handler_chunk_search(self, msg: 'gnes_pb2.Message'):
        from ..indexer.base import BaseChunkIndexer
        if not isinstance(self._model, BaseChunkIndexer):
            raise ServiceError(
                'unsupported indexer, dont know how to use %s to handle this message' % self._model.__bases__)

        results = []
        if not msg.request.search.query.chunks:
            self.logger.warning('query contains no chunks!')
        else:
            results = self._model.query_and_score(msg.request.search.query.chunks, top_k=msg.request.search.top_k)

        self._put_result_into_message(results, msg)

    @handler.register(gnes_pb2.Response.QueryResponse)
    def _handler_doc_search(self, msg: 'gnes_pb2.Message'):
        from ..indexer.base import BaseDocIndexer
        if not isinstance(self._model, BaseDocIndexer):
            raise ServiceError(
                'unsupported indexer, dont know how to use %s to handle this message' % self._model.__bases__)

        # check if chunk_indexer and doc_indexer has the same sorting order
        if msg.response.search.is_big_score_similar is not None and \
                msg.response.search.is_big_score_similar != self._model.is_big_score_similar:
            raise ServiceError(
                'is_big_score_similar is inconsistent. last topk-list: is_big_score_similar=%s, but '
                'this indexer: is_big_score_similar=%s' % (
                    msg.response.search.is_big_score_similar, self._model.is_big_score_similar))

        # assume the doc search will change the whatever sort order the message has
        msg.response.search.is_sorted = False
        results = self._model.query_and_score(msg.response.search.topk_results)
        self._put_result_into_message(results, msg)
