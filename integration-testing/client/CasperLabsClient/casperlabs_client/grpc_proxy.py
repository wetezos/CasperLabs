#!/usr/bin/env python3
import grpc
from concurrent import futures
from threading import Thread
import logging
import re

from . import casperlabs_client, casper_pb2_grpc, gossiping_pb2_grpc, kademlia_pb2_grpc


def read_binary(file_name):
    with open(file_name, "rb") as f:
        return f.read()


def fun_name(o):
    if not o:
        return str(o)
    return str(o).split()[1]


def stub_name(o):
    s = list(filter(None, re.split(r"\W+", str(o))))[-1]
    return s[: -len("ServiceStub")]


def logging_pre_callback(name, request):
    logging.info(f"PROXY PRE CALLBACK: {name} {request}")
    return request


def logging_post_callback(name, request, response):
    logging.info(f"PROXY POST CALLBACK: {name} {request} {response}")
    return response


def logging_post_callback_stream(name, request, response):
    logging.info(f"PROXY POST CALLBACK STREAM: {name} {request} {response}")
    yield from response


class ProxyServicer:
    def __init__(
        self,
        node_host: str = None,
        node_port: int = None,
        proxy_port: int = None,  # just for better logging
        certificate_file: str = None,
        key_file: str = None,
        node_id: str = None,
        service_stub=None,
        pre_callback=logging_pre_callback,
        post_callback=logging_post_callback,
        post_callback_stream=logging_post_callback_stream,
    ):
        self.node_host = node_host
        self.node_port = node_port
        self.proxy_port = proxy_port
        self.certificate_file = certificate_file
        self.key_file = key_file
        self.node_id = node_id or casperlabs_client.extract_common_name(
            certificate_file
        )
        self.service_stub = service_stub
        self.pre_callback = pre_callback
        self.post_callback = post_callback
        self.post_callback_stream = post_callback_stream

        import os

        os.environ[
            "GRPC_SSL_CIPHER_SUITES"
        ] = "HIGH+ECDSA"  # found this when googling for the SSL error message:
        # ssl_transport_security.cc:1238] Handshake failed with fatal error SSL_ERROR_SSL: error:100000f7:SSL routines:OPENSSL_internal:WRONG_VERSION_NUMBER.
        # ssl_transport_security.cc:1238] Handshake failed with fatal error SSL_ERROR_SSL: error:10000410:SSL routines:OPENSSL_internal:SSLV3_ALERT_HANDSHAKE_FAILURE.

        os.environ["GRPC_TRACE"] = "transport_security,tsi"
        os.environ["GRPC_VERBOSITY"] = "DEBUG"

        # See also issue https://github.com/grpc/grpc/issues/9538 opened in Feb 2017, still unresolved at this point of time.

        self.node_address = f"{self.node_host}:{self.node_port}"

        # https://grpc.github.io/grpc/python/grpc.html#create-client-credentials

        """
          root_certificates: The PEM-encoded root certificates as a byte string,
            or None to retrieve them from a default location chosen by gRPC
            runtime.
          private_key: The PEM-encoded private key as a byte string, or None if no
            private key should be used.
          certificate_chain: The PEM-encoded certificate chain as a byte string
            to use or or None if no certificate chain should be used.
        """

        # Channel credentials calls:
        # https://github.com/grpc/grpc/blob/777245d507ceb09b3207533eacb03068a40bac57/src/python/grpcio/grpc/_cython/_cygrpc/credentials.pyx.pxi#L129
        self.credentials = grpc.ssl_channel_credentials(
            root_certificates=read_binary(self.certificate_file),
            private_key=read_binary(self.key_file),
            certificate_chain=None,
        )
        self.secure_channel_options = self.node_id and [
            ("grpc.ssl_target_name_override", self.node_id),
            ("grpc.default_authority", self.node_id),
        ]

    def secure_channel(self):
        # Getting: "Fatal Python error: Aborted"
        # *********** secure_channel: target=localhost:50400, credentials=<grpc.ChannelCredentials object at 0x7ff4c809b278>, options=[('grpc.ssl_target_name_override', '4d802045c3e4d2e031f25878517bc8e2c9710ee7'), ('grpc.default_authority', '4d802045c3e4d2e031f25878517bc8e2c9710ee7')], compression=None
        channel = grpc.secure_channel(
            self.node_address, self.credentials, options=self.secure_channel_options
        )
        logging.info(f"PROXY: secure_channel({self.node_address} => {channel})")
        return channel

    def is_unary_stream(self, method_name):
        return method_name.startswith("Stream")

    def __getattr__(self, name):

        prefix = (
            f"PROXY"
            f" {self.node_host}:{self.node_port} on {self.proxy_port}"
            f" {stub_name(self.service_stub)}::{name}"
        )
        callback_info = (
            f" [{fun_name(self.pre_callback)}"
            f" {fun_name(self.post_callback)}"
            f" {fun_name(self.post_callback_stream)}]"
        )
        logging.info(f"{prefix} {callback_info}")

        def unary_unary(request, context):
            logging.info(f"{prefix}: ({request})")
            with self.secure_channel() as channel:
                preprocessed_request = self.pre_callback(name, request)
                service_method = getattr(self.service_stub(channel), name)
                logging.info(f"{prefix}: service_method = {service_method}")
                response = service_method(preprocessed_request)
                return self.post_callback(name, preprocessed_request, response)

        def unary_stream(request, context):
            logging.info(f"{prefix}: ({request})")
            with self.secure_channel() as channel:
                preprocessed_request = self.pre_callback(name, request)
                streaming_service_method = getattr(self.service_stub(channel), name)
                logging.info(
                    f"{prefix}: streaming_service_method = {streaming_service_method}"
                )
                response_stream = streaming_service_method(preprocessed_request)
                yield from self.post_callback_stream(
                    name, preprocessed_request, response_stream
                )

        return unary_stream if self.is_unary_stream(name) else unary_unary


class ProxyThread(Thread):
    def __init__(
        self,
        service_stub,
        add_servicer_to_server,
        node_host: str,
        node_port: int,
        proxy_port: int,
        certificate_file: str,
        key_file: str,
        node_id: str = None,
        pre_callback=None,
        post_callback=None,
        post_callback_stream=None,
    ):
        super().__init__()
        self.service_stub = service_stub
        self.add_servicer_to_server = add_servicer_to_server
        self.node_host = node_host
        self.node_port = node_port
        self.proxy_port = proxy_port
        self.certificate_file = certificate_file
        self.key_file = key_file
        self.node_id = node_id or casperlabs_client.extract_common_name(
            certificate_file
        )
        self.pre_callback = pre_callback
        self.post_callback = post_callback
        self.post_callback_stream = post_callback_stream

    def run(self):
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
        servicer = ProxyServicer(
            node_host=self.node_host,
            node_port=self.node_port,
            proxy_port=self.proxy_port,
            certificate_file=self.certificate_file,
            key_file=self.key_file,
            node_id=self.node_id,
            service_stub=self.service_stub,
            pre_callback=self.pre_callback,
            post_callback=self.post_callback,
            post_callback_stream=self.post_callback_stream,
        )
        self.add_servicer_to_server(servicer, self.server)
        self.server.add_secure_port(
            f"[::]:{self.proxy_port}",
            grpc.ssl_server_credentials(
                [(read_binary(self.key_file), read_binary(self.certificate_file))]
            ),
        )
        logging.info(
            f"STARTING PROXY: node_port={self.node_port} proxy_port={self.proxy_port}"
        )
        self.server.start()

    def stop(self):
        logging.info(
            f"STOPPING PROXY: node_port={self.node_port} proxy_port={self.proxy_port}"
        )
        self.server.stop(0)


def run_proxy(
    service_stub,
    add_servicer_to_server,
    proxy_port: int,
    node_host: str,
    node_port: int,
    certificate_file: str,
    key_file: str,
    node_id: str = None,
    pre_callback=None,
    post_callback=None,
    post_callback_stream=None,
):
    t = ProxyThread(
        service_stub,
        add_servicer_to_server,
        proxy_port=proxy_port,
        node_host=node_host,
        node_port=node_port,
        certificate_file=certificate_file,
        key_file=key_file,
        node_id=node_id,
        pre_callback=pre_callback,
        post_callback=post_callback,
        post_callback_stream=post_callback_stream,
    )
    t.start()
    return t


def proxy_client(
    node_port=40401,
    node_host="casperlabs",
    proxy_port=50401,
    certificate_file=None,
    key_file=None,
    node_id=None,
    pre_callback=None,
    post_callback=None,
    post_callback_stream=None,
):
    return run_proxy(
        casper_pb2_grpc.CasperServiceStub,
        casper_pb2_grpc.add_CasperServiceServicer_to_server,
        proxy_port=proxy_port,
        node_host=node_host,
        node_port=node_port,
        certificate_file=certificate_file,
        key_file=key_file,
        node_id=node_id or casperlabs_client.extract_common_name(certificate_file),
        pre_callback=pre_callback or logging_pre_callback,
        post_callback=post_callback or logging_post_callback,
        post_callback_stream=post_callback_stream or logging_post_callback_stream,
    )


def proxy_server(
    node_port=50400,
    node_host="casperlabs",
    proxy_port=40400,
    certificate_file=None,
    key_file=None,
    pre_callback=None,
    post_callback=None,
    post_callback_stream=None,
):
    return run_proxy(
        gossiping_pb2_grpc.GossipServiceStub,
        gossiping_pb2_grpc.add_GossipServiceServicer_to_server,
        proxy_port=proxy_port,
        node_host=node_host,
        node_port=node_port,
        certificate_file=certificate_file,
        key_file=key_file,
        pre_callback=pre_callback or logging_pre_callback,
        post_callback=post_callback or logging_post_callback,
        post_callback_stream=post_callback_stream or logging_post_callback_stream,
    )


def kademlia_pre_callback(name, request):
    """
    Patch port: 50404 (the real port of the node behind proxy)
    to 40404 (proxy kademlia).
    """

    logging.info(f"KADEMLIA PRE CALLBACK: {name} {request}")
    try:
        request.sender.protocol_port = 40400
        request.sender.discovery_port = 40404
    except AttributeError:
        logging.info(f"KADEMLIA PRE CALLBACK: NO PATCHING")
    return request


def kademlia_post_callback(name, request, response):
    try:

        def patch(node):
            node.sender.protocol_port = 40400
            node.sender.discovery_port = 40404

        response.nodes = [patch(node) for node in response.nodes]
        logging.info(f"KADEMLIA POST CALLBACK: {name} {request} {response}")
    except AttributeError:
        logging.info(
            f"KADEMLIA POST CALLBACK: {name} {request} {response}  NO PATCHING"
        )

    return response


def proxy_kademlia(
    node_port=50404,
    node_host="127.0.0.1",
    proxy_port=40404,
    certificate_file=None,
    key_file=None,
    pre_callback=None,
    post_callback=None,
    post_callback_stream=None,
):
    return run_proxy(
        kademlia_pb2_grpc.KademliaServiceStub,
        kademlia_pb2_grpc.add_KademliaServiceServicer_to_server,
        proxy_port=proxy_port,
        node_host=node_host,
        node_port=node_port,
        certificate_file=certificate_file,
        key_file=key_file,
        pre_callback=pre_callback or kademlia_pre_callback,
        post_callback=post_callback or kademlia_post_callback,
        post_callback_stream=post_callback_stream or logging_post_callback_stream,
    )
