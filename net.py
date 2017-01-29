from __future__ import print_function
import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F
import numpy as np


class Net(nn.Module):
    def __init__(self, data_vecs, edge_count, dim, slice_size, max_forest_count):
        super(Net, self).__init__()

        # dimension of embeddings
        self.dim = dim

        self.edge_count = edge_count

        self.slice_size = slice_size
        self.max_forest_count = max_forest_count
        self.max_graph_count = (slice_size + 1) * (2 ** (max_forest_count - 1))

        self.data_vecs = {}
        self.data_weights = {}
        self.data_biases = {}
        for data_type in data_vecs.keys():
            vecs = data_vecs[data_type]
            _, vec_dim = vecs.shape
            self.data_vecs[data_type] = Variable(torch.from_numpy(vecs), requires_grad=False)
            self.data_weights[data_type] = Variable(torch.zeros(vec_dim, dim), requires_grad=True)
            self.data_biases[data_type] = Variable(torch.zeros(dim), requires_grad=True)

        self.edge_weights = Variable(torch.zeros(edge_count, dim, dim), requires_grad=True)
        self.edge_biases = Variable(torch.zeros(edge_count, dim), requires_grad=True)
        #for i in range(edge_count):
        #    self.edge_weights[i] = torch.zeros(dim, dim) # Variable(torch.zeros(dim, dim), requires_grad=True)
        #    self.edge_biases[i] = Variable(torch.zeros(dim), requires_grad=True)

        self.score_embedding_weights = Variable(torch.zeros(dim, 1), requires_grad=True)
        self.score_embedding_biases = Variable(torch.zeros(dim), requires_grad=True)
        self.score_data_weights = Variable(torch.zeros(dim, 1), requires_grad=True)
        self.score_data_biases = Variable(torch.zeros(dim), requires_grad=True)

    def calc_embedding(self, data, types, parents, edges):

        # connect roots
        roots = [i[0] for i, parent in np.ndenumerate(parents) if parent == 0]
        for i in range(len(roots) - 1):
            parents[roots[i]] = roots[i + 1]

        root = roots[-1]

        # calc child pointer
        children = {}
        for i, parent in np.ndenumerate(parents):
            i = i[0]
            parent_pos = i + parent
            # skip circle at root pos
            if parent_pos == i:
                continue
            if parent_pos not in children:
                children[parent_pos] = [i]
            else:
                children[parent_pos] += [i]

        return self.calc_embedding_rec(data, types, children, edges, root)

    def calc_embedding_rec(self, data, types, children, edges, idx):
        embedding = torch.mm(self.data_vecs[types[idx]][data[idx]].unsqueeze(0), self.data_weights[types[idx]]) + self.data_biases[types[idx]]
        if idx not in children:     # leaf
            return embedding
        for child in children[idx]:
            embedding += torch.mm(self.calc_embedding_rec(data, types, children, edges, child), self.edge_weights[edges[child]]) \
                         + self.edge_biases[edges[child]]
        return embedding

    def calc_score(self, embedding, data_embedding):
        return torch.mm((self.score_embedding_biases + embedding).unsqueeze(0), self.score_embedding_weights) \
               + torch.mm((self.score_data_biases + data_embedding).unsqueeze(0), self.score_data_weights)

    def forward(self, data, types, edges, graphs):
        pos = len(data) - 1
        t = types[pos]
        d = data[pos]
        data_vec = self.data_vecs[t][d].unsqueeze(0)
        data_embedding = torch.mm(data_vec, self.data_weights[t])
        data_embedding += self.data_biases[t]
        correct_edge = edges[pos]

        scores = Variable(torch.zeros(self.max_graph_count * self.edge_count))
        i = 0
        graph_count, _ = graphs.shape
        for j in range(graph_count):
        #for s, parents in np.ndenumerate(graphs):
            # calc embedding for correct graph at first
            embedding = self.calc_embedding(data, types, graphs[j], edges)
            scores[i] = self.calc_score(embedding, data_embedding)
            i += 1
            for edge in range(self.edge_count):
                if edge == correct_edge:    # already calculated
                    continue
                edges[pos] = edge
                embedding = self.calc_embedding(data, types, graphs[j], edges)
                scores[i] = self.calc_score(embedding, data_embedding)
                i += 1

        # scores = nn.LogSoftmax().forward(scores)
        # scores = F.softmax(scores)
        # expected = torch.zeros(len(scores))
        # expected[0] = 1
        # criterion = nn.CrossEntropyLoss()
        # loss = criterion(scores, expected)

        return scores

    def get_parameters(self):
        return self.data_weights.values() + self.data_biases.values() \
                 + [self.edge_weights, self.edge_biases, self.score_embedding_weights,
                    self.score_embedding_biases, self.score_data_weights, self.score_data_biases]
