"""Process-local simulator egress guard; loopback server traffic stays allowed."""
import ipaddress,socket

def install_simulator_network_guard():
    def local(host):
        if host in {'localhost',None}:return True
        try:return ipaddress.ip_address(host).is_loopback
        except ValueError:return False
    connect,connect_ex,resolve,sendto=socket.socket.connect,socket.socket.connect_ex,socket.getaddrinfo,socket.socket.sendto
    def check(sock,address):
        if sock.family in {socket.AF_INET,socket.AF_INET6} and (not isinstance(address,tuple) or not local(address[0])):raise PermissionError('Simulator external network blocked')
    def guarded_connect(sock,address):check(sock,address);return connect(sock,address)
    def guarded_connect_ex(sock,address):check(sock,address);return connect_ex(sock,address)
    def guarded_resolve(host,*args,**kwargs):
        if not local(host):raise PermissionError('Simulator external DNS blocked')
        return resolve(host,*args,**kwargs)
    def guarded_sendto(sock,*args):check(sock,args[-1]);return sendto(sock,*args)
    socket.socket.connect=guarded_connect;socket.socket.connect_ex=guarded_connect_ex;socket.getaddrinfo=guarded_resolve;socket.socket.sendto=guarded_sendto
