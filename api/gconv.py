import mindspore
import mindspore.nn as nn
import mindspore.ops.operations as P
import mindspore.numpy as np
import mindspore.context as context

context.set_context(device_target="GPU")

def l1norm(A, dim, eps=1e-12):
    m = P.ReduceSum(keep_dims=True)(np.abs(A), axis=dim)
    m = np.maximum(m, eps)
    A = A / m
    return A

class Gconv(nn.Cell):
    """
    Graph Convolutional Layer which is inspired and developed based on Graph Convolutional Network (GCN).
    Inspired by Kipf and Welling. Semi-Supervised Classification with Graph Convolutional Networks. ICLR 2017.
    """
    def __init__(self, in_features, out_features):
        super(Gconv, self).__init__()
        self.num_inputs = in_features
        self.num_outputs = out_features
        self.a_fc = nn.Dense(in_channels=self.num_inputs, out_channels=self.num_outputs)
        self.u_fc = nn.Dense(in_channels=self.num_inputs, out_channels=self.num_outputs)

    def construct(self, A, x, norm=True):
        """
        :param A: connectivity matrix {0,1}^(batch*n*n)
        :param x: node embedding batch*n*d
        :param norm: normalize connectivity matrix or not
        :return: new node embedding
        """
        if norm is True:
            A = l1norm(A, dim=-2)
        ax = self.a_fc(x)
        ux = self.u_fc(x)
        x = P.BatchMatMul()(A, P.ReLU()(ax)) + P.ReLU()(ux) # has size (bs, N, num_outputs)
        return x

class ChannelIndependentConv(nn.Cell):
    """
    Channel Independent Embedding Convolution
    Proposed by Yu et al. Learning deep graph matching with channel-independent embedding and Hungarian attention. ICLR 2020.
    """
    def __init__(self, in_features, out_features, in_edges, out_edges=None):
        super(ChannelIndependentConv, self).__init__()
        if out_edges is None:
            out_edges = out_features
        self.in_features = in_features
        self.out_features = out_features
        self.out_edges = out_edges
        self.node_fc = nn.Dense(in_channels=in_features, out_channels=out_features)
        self.node_sfc = nn.Dense(in_channels=in_features, out_channels=out_features)
        self.edge_fc = nn.Dense(in_channels=in_edges, out_channels=self.out_edges)

    def construct(self, A, emb_node, emb_edge, mode=1):
        """
        :param A: connectivity matrix {0,1}^(batch*n*n)
        :param emb_node: node embedding batch*n*d
        :param emb_edge: edge embedding batch*n*n*d
        :param mode: 1 or 2
        :return: new node embedding, new edge embedding
        """
        if mode == 1:
            node_x = self.node_fc(emb_node)
            node_sx = self.node_sfc(emb_node)
            edge_x = self.edge_fc(emb_edge)

            A = P.ExpandDims()(A,-1)
            A = P.Mul()(P.BroadcastTo(edge_x.shape)(A), edge_x)
            perm1 = tuple(range(4, len(A.shape)))
            perm1 = (0, 3, 1, 2) + perm1
            perm2 = tuple(range(4, len(node_x.shape)+1))
            perm2 = (0, 3, 1, 2) + perm2
            node_x = np.matmul(P.Transpose()(A,perm1),
                                  P.Transpose()(P.ExpandDims()(node_x, 2), perm2))
            perm = tuple(range(3, len(node_x.shape)-1))
            perm = (0, 2, 1) + perm
            node_x = P.Transpose()(P.ReduceMean(-1)(node_x), perm)
            node_x = P.ReLU()(node_x) + P.ReLU()(node_sx)
            edge_x = P.ReLU()(edge_x)

            return node_x, edge_x

        elif mode == 2:
            node_x = self.node_fc(emb_node)
            node_sx = self.node_sfc(emb_node)
            edge_x = self.edge_fc(emb_edge)

            d_x = P.ExpandDims()(node_x, 1) - P.ExpandDims()(node_x, 2)
            d_x = P.ReduceSum()(d_x ** 2, 3)
            d_x = P.Exp()(-d_x)

            A = P.ExpandDims()(A, -1)
            A = P.Mul()(P.BroadcastTo(edge_x.shape)(A), edge_x)

            perm1 = tuple(range(4, len(A.shape)))
            perm1 = (0, 3, 1, 2) + perm1
            perm2 = tuple(range(4, len(node_x.shape) + 1))
            perm2 = (0, 3, 1, 2) + perm2
            node_x = np.matmul(P.Transpose()(A, perm1),
                               P.Transpose()(P.ExpandDims()(node_x, 2), perm2))
            perm = tuple(range(3, len(node_x.shape) - 1))
            perm = (0, 2, 1) + perm
            node_x = P.Transpose()(P.ReduceMean(-1)(node_x), perm)
            node_x = P.ReLU()(node_x) + P.ReLU()(node_sx)
            edge_x = P.ReLU()(edge_x)
            return node_x, edge_x, d_x

class Siamese_Gconv(nn.Cell):
    def __init__(self, in_features, num_features):
        super(Siamese_Gconv, self).__init__()
        self.gconv = Gconv(in_features, num_features)

    def construct(self, g1, *args):
        # embx are tensors of size (bs, N, num_features)
        emb1 = self.gconv(*g1)
        if len(args) == 0:
            return emb1
        else:
            returns = [emb1]
            for g in args:
                returns.append(self.gconv(*g))
            return returns

class Siamese_ChannelIndependentConv(nn.Cell):
    def __init__(self, in_features, num_features, in_edges, out_edges=None):
        super(Siamese_ChannelIndependentConv, self).__init__()
        self.in_feature = in_features
        self.gconv1 = ChannelIndependentConv(in_features, num_features, in_edges, out_edges)
        self.gconv2 = ChannelIndependentConv(in_features, num_features, in_edges, out_edges)

    def construct(self, g1, g2=None):
        emb1, emb_edge1 = self.gconv1(*g1)
        if g2 is None:
            return emb1, emb_edge1
        else:
            emb2, emb_edge2 = self.gconv2(*g2)
            # embx are tensors of size (bs, N, num_features)
            return emb1, emb2, emb_edge1, emb_edge2
