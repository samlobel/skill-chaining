import tensorflow as tf
import numpy as np
import random
import tflearn
import os
from numpy.linalg import norm

from simple_rl.agents.func_approx.dsc.OptionClass import Option

class CoveringOptions(Option):
    # This class identifies a subgoal by Laplacian method.
    # We feed this option to the skill chaining as a parent and generate its child options.
    
    def __init__(self, replay_buffer, obs_dim, feature=None, threshold=0.8, num_units=200, num_training_steps=1000, actor_learning_rate=0.0001, critic_learning_rate=0.0001, batch_size=64, option_idx=None, name="covering-options"):
        self.obs_dim = obs_dim
        self.threshold = threshold
        self.num_units = num_units
        self.num_training_steps = num_training_steps
        self.batch_size = batch_size
        self.option_idx = option_idx
        self.name = name
        
        if feature == "fourier":
            self.feature = Fourier(obs_dim, (-np.ones(obs_dim), np.ones(obs_dim)), 3)
        else:
            self.feature = None

        if self.feature is None:
            indim = self.obs_dim
        else:
            indim = self.feature.num_features()
            
        self.initiation_classifier = SpectrumNetwork(obs_dim=indim, training_steps=self.num_training_steps, n_units=self.num_units, conv=False, name=self.name + "-spectrum")

        self.initiation_classifier.initialize()
        self.train(replay_buffer)
        
        self.threshold_value = self.sample_f_val(replay_buffer)

        
        # Parameters to be set to child options.
        # We don't use them for covering options.
        class mock():
            def __init__(self):
                self.actor_learning_rate = actor_learning_rate
                self.critic_learning_rate = critic_learning_rate
                self.batch_size = batch_size
                
        self.solver = mock()

    def states_to_tensor(self, states):
        obs = []
        for state in states:
            if self.feature is None:
                # debugging the NN
                # o = np.zeros_like(state.data)
                # o[0] = state.data[0]
                # o[1] = state.data[1]
                o = state.data
            else:
                o = self.feature.feature(state, 0)
            obs.append(o)
        return obs

    def train(self, replay_buffer):
        for _ in range(self.num_training_steps):
            s, a, r, s2, t, _ = replay_buffer.sample(self.batch_size)

            s = s.numpy()
            s2 = s2.numpy()
            
            # obs = list(obs)
            # print('s2=', s2)
            # obs2 = list(obs2)
            # print('obs2=', obs2)
            # s = list(s)
            # s2 = list(s2)
            obs2 = self.states_to_tensor(s2)
            
            next_f_value = self.initiation_classifier(obs2)

            obs = self.states_to_tensor(s)
            
            self.initiation_classifier.train(obs, next_f_value)
            
        
    def is_init_true(self, ground_state):
        s = self.states_to_tensor([ground_state])
        # print('s=', s)
        # print('s=', type(s))
        return self.initiation_classifier(s) > self.threshold_value

    def batched_is_init_true(self, state_matrix):
        x = self.initiation_classifier(state_matrix) > self.threshold_value
        return x.flatten()

    def is_term_true(self, ground_state):
        # TODO: set termination condition the same as the initiation condition.
        return self.is_init_true(ground_state)

    def sample_f_val(self, experience_buffer):
        buf_size = len(experience_buffer)

        n_samples = min(buf_size, 2048)
        # n_samples = buf_size

        # s = [experience_buffer.memory[i][0] for i in range(len(experience_buffer.memory))]
        s, _, _, _, _, _ = experience_buffer.sample(n_samples)
        s = s.numpy()
        obs = self.states_to_tensor(s)
        f_values = self.initiation_classifier(obs)
        if type(f_values) is list:
            f_values = np.asarray(f_values)
        # print('fvalue=', f_values)
        f_values = f_values.flatten()

        f_srt = np.sort(f_values)
        
        print('f_srt=', f_srt)

        # print('n_samples=', n_samples)
        # print('len(s)=', len(s))
        # print('f_value=', len(f_values))
        
        init_th = f_srt[int(n_samples * self.threshold)]

        print('init_th =', init_th)

        return init_th


class SpectrumNetwork():

    def __init__(self, obs_dim=None, learning_rate=0.0001, training_steps=100, batch_size=32, n_units=16, beta=0.0, delta=0.1, conv=False, name="spectrum"):
        # Beta  : Lagrange multiplier. Higher beta would make the vector more orthogonal.
        # delta : Orthogonality parameter.
        self.sess = tf.Session()
        self.learning_rate = learning_rate
        self.obs_dim = obs_dim

        self.n_units = n_units
        
        # self.beta = 1000000.0
        self.beta = beta
        self.delta = 0.05
        # self.delta = delta

            

        self.conv = conv
        
        self.name = name

        self.obs, self.f_value = self.network(scope=name+"_eval")

        self.next_f_value = tf.placeholder(tf.float32, [None, 1], name=name+"_next_f")

        # TODO: Is this what we are looking for?
        self.loss = tflearn.mean_square(self.f_value, self.next_f_value) \
                    + self.beta * tf.reduce_mean(tf.multiply(self.f_value - self.delta, self.next_f_value - self.delta)) \
                    + self.beta * tf.reduce_mean(self.f_value * self.f_value * self.next_f_value * self.next_f_value) \
                    + self.beta * tf.math.maximum((self.f_value - self.next_f_value), 0.0) # This is to enforce f(s) <= f(s').
        
        # with tf.variable_scope(self.name, reuse=tf.AUTO_REUSE):
        self.optimizer = tf.train.AdamOptimizer(learning_rate)
            
        self.optimize = self.optimizer.minimize(self.loss)

        self.network_params = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope=name + "_eval")
        self.initializer = tf.initializers.variables(self.network_params + self.optimizer.variables())

        # print('network param names for ', self.name)
        # for n in self.network_params:
        #     print(n.name)
            
        self.saver = tf.train.Saver(self.network_params)

    def network(self, scope):
        indim = self.obs_dim
        obs = tf.placeholder(tf.float32, [None, indim], name=self.name+"_obs")

        with tf.variable_scope(scope, reuse=tf.AUTO_REUSE):
            if self.conv:
                reshaped_obs = tf.reshape(obs, [-1, 105, 80, 3])
                net = tflearn.conv_2d(reshaped_obs, 32, 8, strides=4, activation='relu')
                net = tflearn.conv_2d(net, 64, 4, strides=2, activation='relu')
                out = tflearn.fully_connected(net, 1, weights_init=tflearn.initializations.uniform(minval=-0.003, maxval=0.003))
            else:
                # net = tflearn.fully_connected(obs, self.n_units, name='d1', weights_init=tflearn.initializations.truncated_normal(stddev=1.0/float(indim)))
                # net = tflearn.fully_connected(net, self.n_units, name='d2', weights_init=tflearn.initializations.truncated_normal(stddev=1.0/float(self.n_units)))
                # net = tflearn.fully_connected(net, self.n_units, name='d3', weights_init=tflearn.initializations.truncated_normal(stddev=1.0/float(self.n_units)))
                # net = tflearn.layers.normalization.batch_normalization(net)
                # net = tf.contrib.layers.batch_norm(net)
                # net = tflearn.activations.relu(net)

                w_init = tflearn.initializations.uniform(minval=-0.003, maxval=0.003)
                # net = tflearn.fully_connected(net, 1, weights_init=w_init)
                net = tflearn.fully_connected(obs, 1, weights_init=w_init)
                out = net
        return obs, out

    def train(self, obs, next_f_value):

        # print('next_f_value=', next_f_value)
        # print('type(obs)=', type(obs))
        # print('type(next_f_value)=', type(next_f_value))
        self.sess.run(self.optimize, feed_dict={
            self.obs: obs,
            self.next_f_value: next_f_value
        })

    def initialize(self):
        self.sess.run(self.initializer, feed_dict={})

    def f_ret(self, state):
        return self.sess.run(self.f_value, feed_dict={
            self.obs: state
        })

    def f_from_features(self, features):
        assert(isinstance(features, np.ndarray))
        return self.sess.run(self.f_value, feed_dict={
            self.obs: features
        })
    
    def __call__(self, obs):
        assert(isinstance(obs, list))
                    
        r = self.f_ret(obs)
        return r

    def restore(self, directory, name='spectrum_nn'):
        self.saver.restore(self.sess, directory + '/' + name)
    
    def save(self, directory, name='spectrum_nn'):
        self.saver.save(self.sess, directory + '/' + name)

    
class Fourier(object):
    def __init__(self, state_dim, bound, order):
        assert(type(state_dim) is int)
        assert(type(order) is int)

        assert(state_dim == bound[0].shape[0])
        assert(state_dim == bound[1].shape[0])
        
        self.state_dim = state_dim
        self.state_up_bound = bound[0]
        self.state_low_bound = bound[1]
        self.order = order


        self.coeff = np.indices((self.order,) * self.state_dim).reshape((self.state_dim, -1)).T

        n = np.array(list(map(norm, self.coeff)))
        n[0] = 1.0
        self.norm = 1.0 / n
        
    def feature(self, state, action):
        xf = state.data
        assert(len(xf) == self.state_dim)

        norm_state = (xf + self.state_low_bound) / (self.state_up_bound - self.state_low_bound)
        
        f_np = np.cos(np.pi * np.dot(self.coeff, norm_state))

        # Check if the weights are set to numbers
        assert(not np.isnan(np.sum(f_np)))

        return f_np.tolist()

    def alpha(self):
        return self.norm

    def num_features(self):
        # What is the number of features for Fourier?
        return self.order**(self.state_dim)
